"""Model Diplomat threads become the customer log. MONGO_URI is read from the environment."""

from __future__ import annotations

import os
from typing import Any

from pymongo import MongoClient

from searchaudit.gap.log import row_from_turn, write_jsonl


def export_log(uri: str, dest, *, limit: int = 0) -> int:
    client = MongoClient(uri, serverSelectionTimeoutMS=20000)
    turns = list(client["search"]["turn_quality"].find(
        {"cost_mc": {"$gt": 0}, "query": {"$nin": [None, ""]}},
        {"user_hash": 0, "trace_url": 0, "ticket_issue_id": 0, "ticket_issue_identifier": 0},
    ))
    if limit:
        turns = turns[:limit]
    thread_ids = list({t.get("thread_id") for t in turns if t.get("thread_id")})
    threads: dict[str, dict] = {}
    col = client["search"]["thread"]
    for start in range(0, len(thread_ids), 100):
        chunk = thread_ids[start:start + 100]
        for doc in col.find({"_id": {"$in": chunk}}, {"messages": 1}):
            threads[str(doc["_id"])] = doc
    rows: list[dict[str, Any]] = []
    for turn in turns:
        row = row_from_turn(turn, threads.get(str(turn.get("thread_id") or "")))
        if row:
            rows.append(row)
    write_jsonl(dest, rows)
    return len(rows)


def main() -> None:
    from pathlib import Path

    uri = os.environ.get("MONGO_URI", "")
    if not uri:
        raise SystemExit("MONGO_URI is not set")
    dest = Path(os.environ.get("DIPLOMAT_LOG", "audits/diplomat/log.jsonl"))
    n = export_log(uri, dest)
    print(f"wrote {n} rows to {dest}")


if __name__ == "__main__":
    main()
