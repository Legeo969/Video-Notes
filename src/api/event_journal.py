"""Persistent job event journal.

The journal lives in the same SQLite database as ``processing_runs``. This
keeps task state, request snapshots and user-visible events in one transaction
boundary and avoids the former split-brain ``~/.video-notes-ai/event_journal``
database.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from src.application.services.job_queue import get_default_db_path


def _get_default_journal_path() -> str:
    return get_default_db_path("./output")


class EventJournal:
    """Thread-safe append-only event log for job lifecycle notifications."""

    def __init__(self, db_path: str | None = None):
        self._db_path = os.path.abspath(db_path or _get_default_journal_path())
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    data TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_events_run_id "
                "ON job_events(run_id, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_events_job_id "
                "ON job_events(job_id, id)"
            )
            conn.commit()

    @staticmethod
    def _decode_data(raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {"value": value}
        except (TypeError, json.JSONDecodeError):
            return {}

    def _identity(self, conn: sqlite3.Connection, job_id: str | int) -> tuple[int | None, str]:
        try:
            run_id = int(job_id)
        except (TypeError, ValueError):
            return None, str(job_id)

        try:
            row = conn.execute(
                "SELECT job_id FROM processing_runs WHERE id = ?", (run_id,)
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        return run_id, str(row["job_id"] if row and row["job_id"] else job_id)

    def append(
        self,
        job_id: str | int,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(data or {}, ensure_ascii=False)
        with self._lock:
            with self._connect() as conn:
                run_id, stable_job_id = self._identity(conn, job_id)
                cursor = conn.execute(
                    """
                    INSERT INTO job_events
                        (run_id, job_id, event_type, data, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, stable_job_id, event_type, payload, now),
                )
                conn.commit()
                return int(cursor.lastrowid or 0)

    def events_since(
        self,
        job_id: str | int,
        last_event_id: int = 0,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            run_id, stable_job_id = self._identity(conn, job_id)
            if run_id is not None:
                rows = conn.execute(
                    """
                    SELECT id, event_type, data, created_at
                    FROM job_events
                    WHERE (run_id = ? OR job_id = ?) AND id > ?
                    ORDER BY id ASC
                    """,
                    (run_id, stable_job_id, last_event_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, event_type, data, created_at
                    FROM job_events
                    WHERE job_id = ? AND id > ?
                    ORDER BY id ASC
                    """,
                    (stable_job_id, last_event_id),
                ).fetchall()
            return [
                {
                    "id": row["id"],
                    "event_type": row["event_type"],
                    "data": self._decode_data(row["data"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def events_for_job(self, job_id: str | int, limit: int = 100) -> list[dict[str, Any]]:
        events = self.events_since(job_id, 0)
        return list(reversed(events[-max(0, int(limit)):]))

    def all_events(self, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, run_id, job_id, event_type, data, created_at
                FROM job_events
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "run_id": row["run_id"],
                    "job_id": row["job_id"],
                    "event_type": row["event_type"],
                    "data": self._decode_data(row["data"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM job_events").fetchone()
            return int(row["cnt"] if row else 0)

    def prune(self, before_id: int) -> int:
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute("DELETE FROM job_events WHERE id < ?", (before_id,))
                conn.commit()
                return max(cursor.rowcount, 0)
