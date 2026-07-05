"""Persistent event journal for job events.

Stores events in SQLite so they survive engine restarts.
Events: job.created, job.started, job.progress, job.stage_completed,
        job.paused, job.cancelled, job.failed, job.completed, etc.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from src.config.constants import DEFAULT_SETTINGS_DIRNAME

_EVENT_JOURNAL_DIRNAME = DEFAULT_SETTINGS_DIRNAME
_EVENT_JOURNAL_FILENAME = "event_journal.db"


def _get_default_journal_path() -> str:
    return os.path.join(
        os.path.expanduser("~"),
        _EVENT_JOURNAL_DIRNAME,
        _EVENT_JOURNAL_FILENAME,
    )


class EventJournal:
    """持久化事件日志，记录任务生命周期中的状态变更。

    线程安全（使用 threading.Lock 保护 SQLite 写入）。
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _get_default_journal_path()
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """初始化事件表。"""
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS event_journal (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id      TEXT NOT NULL,
                    event_type  TEXT NOT NULL,
                    data        TEXT NOT NULL DEFAULT '{}',
                    created_at  TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_event_job_id
                ON event_journal(job_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_event_created
                ON event_journal(created_at)
            """)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def append(
        self,
        job_id: str | int,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> int:
        """追加一条事件记录。

        Args:
            job_id: 关联的任务 ID（可以是 run_id 或 UUID）。
            event_type: 事件类型，如 ``job.created``、``job.progress`` 等。
            data: 附加数据（可选）。

        Returns:
            事件记录的 ID。
        """
        now = datetime.now(timezone.utc).isoformat()
        data_json = json.dumps(data or {}, ensure_ascii=False)

        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO event_journal (job_id, event_type, data, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (str(job_id), event_type, data_json, now),
                )
                conn.commit()
                row_id = cursor.lastrowid
        return row_id if row_id is not None else 0

    def events_since(
        self,
        job_id: str | int,
        last_event_id: int = 0,
    ) -> list[dict[str, Any]]:
        """获取自 last_event_id 之后的所有事件。

        Args:
            job_id: 任务 ID。
            last_event_id: 上次看到的事件 ID（返回比此更大的事件）。

        Returns:
            事件 dict 列表，每个包含 id/event_type/data/created_at。
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, event_type, data, created_at
                FROM event_journal
                WHERE job_id = ? AND id > ?
                ORDER BY id ASC
                """,
                (str(job_id), last_event_id),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "event_type": row["event_type"],
                    "data": json.loads(row["data"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def events_for_job(
        self,
        job_id: str | int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """获取任务的全部事件（最新优先）。

        Args:
            job_id: 任务 ID。
            limit: 最大返回条数。

        Returns:
            事件 dict 列表。
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, event_type, data, created_at
                FROM event_journal
                WHERE job_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(job_id), limit),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "event_type": row["event_type"],
                    "data": json.loads(row["data"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def all_events(
        self,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """获取所有事件（全局视角，最新优先）。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, job_id, event_type, data, created_at
                FROM event_journal
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "job_id": row["job_id"],
                    "event_type": row["event_type"],
                    "data": json.loads(row["data"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def count(self) -> int:
        """返回事件总数。"""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM event_journal").fetchone()
            return row["cnt"] if row else 0

    def prune(self, before_id: int) -> int:
        """删除 ID 小于 before_id 的旧事件。

        Returns:
            删除的行数。
        """
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM event_journal WHERE id < ?",
                    (before_id,),
                )
                conn.commit()
                return cursor.rowcount
