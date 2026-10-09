"""Build the gap page payload and the loss file from the log, the sample, and SELECT runs."""

from __future__ import annotations

import json
import re
from pathlib import Path

from searchaudit.gap.log import CLASSES, coherent, read_jsonl, require_columns
from searchaudit.gap.loss import miss_rows, render_csv
from searchaudit.gap.sample import headline_inputs, spotcheck, stratified

SQL_MARK = re.compile(r"WEB_SEARCH|SEM_EXTRACT", re.IGNORECASE)
PARSE_ERROR = "SQL parse error"


def sql_ok(tool_calls: list[dict]) -> bool:
    selects = [call for call in tool_calls if call.get("tool") == "select"]
    if not selects:
        return False
    for call in selects:
        query = str((call.get("args") or {}).get("query") or "")
        if PARSE_ERROR in str(call.get("error") or ""):
            return False
        if not SQL_MARK.search(query):
            return False
    return True


def _by_turn(rows: list[dict]) -> dict[str, dict]:
    return {row["turn_id"]: row for row in rows if row.get("turn_id")}


def assemble(log_path: Path, runs_path: Path, out_dir: Path) -> dict:
    log_rows = [row for row in read_jsonl(log_path) if coherent(row)]
    for row in log_rows:
        require_columns(row)
    sample, weights = stratified(log_rows)
    runs = _by_turn(read_jsonl(runs_path))
    classes = headline_inputs(log_rows)
    loss: list[dict] = []
    tasks = []
    correct = {name: 0 for name in CLASSES}
    judged = {name: 0 for name in CLASSES}
    select_spend = {name: 0.0 for name in CLASSES}
    exa_spend = {name: 0.0 for name in CLASSES}
    for row in sample:
        run = runs.get(row.get("turn_id") or "")
        grade = (run or {}).get("grade")
        calls = (run or {}).get("tool_calls") or []
        select_urls: list[str] = []
        for call in calls:
            select_urls.extend(call.get("urls") or [])
        select_text = (run or {}).get("answer") or ""
        if run:
            loss.extend(miss_rows(row, select_urls, select_text + "\n" + "\n".join(
                str(call.get("result") or "") for call in calls)))
            name = row["class"]
            judged[name] += 1
            select_spend[name] += float(run.get("cost_usd") or 0)
            exa_spend[name] += float(row["dollars"])
            if grade == "c":
                correct[name] += 1
        sql = ""
        for call in calls:
            if call.get("tool") == "select":
                sql = str((call.get("args") or {}).get("query") or "")
                break
        tasks.append({
            "task": row["task"],
            "queries": row["queries"],
            "provider": row["provider"],
            "dollars": row["dollars"],
            "timestamp": row["timestamp"],
            "class": row["class"],
            "urls": row.get("urls") or [],
            "answer": row.get("answer") or "",
            "turn_id": row.get("turn_id") or "",
            "select_sql": sql,
            "select_ok": sql_ok(calls) if run else False,
            "grade": grade,
            "select_cost_usd": (run or {}).get("cost_usd"),
        })
    class_rows = []
    for name in CLASSES:
        info = classes[name]
        n_correct = correct[name]
        show = info["rate"] and judged[name] > 0
        class_rows.append({
            "class": name,
            "n_log": info["n"],
            "weight": round(weights.get(name, 0.0), 3),
            "judged": judged[name],
            "match_rate": round(n_correct / judged[name], 3) if show else None,
            "usd_per_correct": round(select_spend[name] / n_correct, 4) if show and n_correct else None,
            "exa_usd_per_correct": round(exa_spend[name] / n_correct, 4) if show and n_correct else None,
            "rate_shown": bool(show),
        })
    opened = max(tasks, key=lambda row: len(row["queries"])) if tasks else None
    payload = {
        "kind": "gap",
        "company": "Model Diplomat",
        "opened": opened,
        "classes": class_rows,
        "tasks": tasks,
        "spotcheck": spotcheck(log_rows),
        "n_log": len(log_rows),
        "n_ran": sum(judged.values()),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gap.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    (out_dir / "loss.csv").write_text(render_csv(loss))
    return payload
