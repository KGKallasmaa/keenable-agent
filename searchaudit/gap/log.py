"""Customer-log columns: task, queries, provider, dollars, timestamp.

Model Diplomat is the first file. Extra fields (class, urls, answer) ride along so the
sample and the loss file can be built without a second export.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

COLUMNS = ("task", "queries", "provider", "dollars", "timestamp")
CLASSES = (
    "fact lookup",
    "entity profile",
    "news/recency",
    "list/compare",
    "how-to",
    "long-tail",
)
MIN_CLASS = 10


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def require_columns(row: dict) -> None:
    missing = [name for name in COLUMNS if name not in row]
    if missing:
        raise ValueError(f"log row missing {missing}")
    if not isinstance(row["queries"], list) or not row["task"]:
        raise ValueError("log row needs a task and a list of queries")
    if not isinstance(row["dollars"], int | float):
        raise ValueError("dollars must be a number from the trace")


def micro_cents_to_usd(cost_mc: int | None) -> float | None:
    """Model Diplomat stores the usage-ledger total in micro-cents (1_000_000 = $1)."""
    if cost_mc is None:
        return None
    return round(int(cost_mc) / 1_000_000, 6)


def tag_class(task: str, *, current_events: bool = False) -> str:
    """One of the six classes. Rules, so a spot-check of 30 can correct them before rates are trusted."""
    text = " ".join(task.lower().split())
    if current_events or any(w in text for w in ("latest", "today", "this week", "breaking", "just announced")):
        return "news/recency"
    if any(w in text for w in ("how to", "how do i", "how do you", "how can", "steps to")):
        return "how-to"
    if any(w in text for w in ("compare", " vs ", "versus", "list of", "list the", "all the", "which of")):
        return "list/compare"
    if any(w in text for w in ("who is", "profile of", "background of", "biography")):
        return "entity profile"
    words = text.split()
    if len(words) <= 14 and any(text.startswith(w) for w in ("what", "when", "where", "who", "which")):
        return "fact lookup"
    if len(words) <= 8:
        return "fact lookup"
    return "long-tail"


def tag_log(rows: list[dict]) -> list[dict]:
    tagged = []
    for row in rows:
        require_columns(row)
        nxt = dict(row)
        nxt["class"] = nxt.get("class") or tag_class(nxt["task"], current_events=bool(nxt.get("current_events")))
        if nxt["class"] not in CLASSES:
            raise ValueError(f"unknown class {nxt['class']}")
        tagged.append(nxt)
    return tagged


def search_queries(assistant: dict) -> list[str]:
    found = []
    for step in assistant.get("steps") or []:
        if not isinstance(step, dict) or step.get("kind") != "tool":
            continue
        args = step.get("arguments") or {}
        label = str(step.get("label") or "")
        query = args.get("query")
        if not query:
            continue
        if label.startswith("Searching the web") or step.get("icon") == "globe":
            found.append(str(query))
    return found


def source_rows(assistant: dict) -> list[dict[str, str]]:
    raw = assistant.get("search_context") or ""
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    rows = []
    for source in data.get("sources") or []:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "")
        if url.startswith("http"):
            rows.append({"url": url, "title": str(source.get("title") or "")})
    return rows


def _assistant_for(thread: dict, query: str) -> tuple[str, dict] | None:
    needle = " ".join((query or "").split())
    if not needle:
        return None
    messages = thread.get("messages") or []
    for index, message in enumerate(messages):
        if message.get("role") != "user":
            continue
        text = " ".join((((message.get("message") or {}).get("query")) or "").split())
        if not text or not (text == needle or text.startswith(needle) or needle.startswith(text[:80])):
            continue
        for later in messages[index + 1:]:
            if later.get("role") == "user":
                break
            if later.get("role") == "assistant":
                return text, later.get("message") or {}
    return None


def row_from_turn(turn: dict, thread: dict | None) -> dict | None:
    """One customer-log row from a turn_quality document and its thread, or None when it is not a search loop."""
    if not thread:
        return None
    dollars = micro_cents_to_usd(turn.get("cost_mc"))
    if dollars is None:
        return None
    paired = _assistant_for(thread, str(turn.get("query") or ""))
    if paired is None:
        return None
    task, assistant = paired
    queries = search_queries(assistant)
    if not queries:
        return None
    started = turn.get("turn_started_at")
    if isinstance(started, datetime):
        timestamp = started.isoformat()
    else:
        timestamp = str(started or "")
    answer = (assistant.get("answer") or {}) if isinstance(assistant.get("answer"), dict) else {}
    return {
        "task": task,
        "queries": queries,
        "provider": "exa",
        "dollars": dollars,
        "timestamp": timestamp,
        "class": tag_class(task, current_events=bool(turn.get("is_current_events"))),
        "urls": source_rows(assistant),
        "answer": str(answer.get("text") or "")[:6000],
        "thread_id": str(turn.get("thread_id") or ""),
        "turn_id": str(turn.get("_id") or ""),
        "current_events": bool(turn.get("is_current_events")),
    }


_FOLLOWUP = re.compile(
    r"^(too long|correct it|answer to this|make it|shorter|it'?s too)\b", re.IGNORECASE
)


def coherent(row: dict) -> bool:
    """The searches are about this task. A follow-up fragment joined to another turn's loop is not a row."""
    task = " ".join(str(row.get("task") or "").split())
    if len(task.split()) < 6 or _FOLLOWUP.match(task):
        return False
    words = [w for w in re.findall(r"[a-z0-9]{4,}", task.lower()) if w not in {"that", "this", "with", "from", "your", "have", "been", "what", "when", "where", "which", "about", "into", "some", "make", "more", "like", "just"}]
    blob = " ".join(row.get("queries") or []).lower()
    return any(word in blob for word in words)


def class_counts(rows: list[dict]) -> dict[str, int]:
    counts = {name: 0 for name in CLASSES}
    for row in rows:
        counts[row["class"]] = counts.get(row["class"], 0) + 1
    return counts


def rate_allowed(count: int) -> bool:
    return count >= MIN_CLASS


def stamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")
