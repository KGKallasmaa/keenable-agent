"""Label a miss the trace had and SELECT did not.

index gap: the URL is absent from SELECT evidence.
extraction miss: the URL is present and the value is wrong.
A URL with no label is not a row. A row with an empty label fails the check.
"""

from __future__ import annotations

import csv
import io
import re

LABELS = ("index gap", "extraction miss")
_TOKEN = re.compile(r"[a-z0-9]{5,}")


def norm_url(url: str) -> str:
    return url.split("#", 1)[0].rstrip("/").lower()


def value_wrong(title: str, select_text: str) -> bool:
    """The page was retrieved but its title's distinctive words are missing from SELECT's text."""
    tokens = _TOKEN.findall(title.lower())
    if not tokens:
        return True
    text = select_text.lower()
    hits = sum(1 for token in tokens if token in text)
    return hits * 2 < len(tokens)


def label_miss(url: str, select_urls: set[str], *, value_wrong: bool) -> str | None:
    """None when this URL is not a miss. Otherwise one of the two labels."""
    if norm_url(url) not in {norm_url(u) for u in select_urls}:
        return "index gap"
    if value_wrong:
        return "extraction miss"
    return None


def miss_rows(task: dict, select_urls: list[str], select_text: str) -> list[dict]:
    rows = []
    for source in task.get("urls") or []:
        url = str(source.get("url") or "")
        if not url:
            continue
        label = label_miss(url, set(select_urls), value_wrong=value_wrong(str(source.get("title") or ""), select_text))
        if label is None:
            continue
        if label not in LABELS:
            raise ValueError(f"unlabeled miss for {url}")
        rows.append({
            "task": task["task"],
            "url": url,
            "title": source.get("title") or "",
            "label": label,
            "class": task.get("class") or "",
            "dollars": task["dollars"],
        })
    return rows


def render_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    fields = ["task", "url", "title", "label", "class", "dollars"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        if row.get("label") not in LABELS:
            raise ValueError("loss row is missing its label")
        if row.get("url") and not row.get("label"):
            raise ValueError("URL-only loss row")
        writer.writerow({name: row.get(name, "") for name in fields})
    return buf.getvalue()
