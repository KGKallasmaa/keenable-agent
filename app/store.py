import json
import sqlite3
import threading
import uuid
from pathlib import Path


class RunStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "runs.db"
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def create(self, payload: dict) -> str:
        run_id = uuid.uuid4().hex[:12]
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (id, status, payload) VALUES (?, ?, ?)",
                (run_id, "queued", json.dumps(payload)),
            )
        return run_id

    def update(self, run_id: str, status: str, payload: dict) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, payload = ? WHERE id = ?",
                (status, json.dumps(payload), run_id),
            )

    def get(self, run_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, status, payload, created_at FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        body = json.loads(row["payload"])
        body["id"] = row["id"]
        body["status"] = row["status"]
        body["created_at"] = row["created_at"]
        return body

    def latest(self) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return self.get(row["id"])

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn
