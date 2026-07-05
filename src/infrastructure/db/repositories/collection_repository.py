"""CollectionRepository — CRUD for collections + collection_items tables.

Does NOT manage transactions — the caller (e.g. CollectionService)
is responsible for commit/rollback.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CollectionRepository:
    """Encapsulates all SQL access for collections + collection_items tables."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ── collections CRUD ─────────────────────────────────────

    def insert_collection(
        self,
        collection_id: str,
        title: str,
        collection_type: str = "course",
        description: str | None = None,
        template_id: str | None = None,
        output_dir: str | None = None,
    ) -> None:
        """INSERT INTO collections. Raises on UNIQUE violation."""
        now = _now()
        self._conn.execute(
            """INSERT INTO collections
               (collection_id, title, description, collection_type,
                template_id, output_dir, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (collection_id, title, description, collection_type,
             template_id, output_dir, now, now),
        )

    def list_collections(self) -> list[dict]:
        """SELECT all collections ORDER BY created_at DESC."""
        rows = self._conn.execute(
            """SELECT id, collection_id, title, description, collection_type,
                      template_id, output_dir, created_at, updated_at
               FROM collections
               ORDER BY created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_collection_by_id(self, collection_id: str) -> dict | None:
        """SELECT by collection_id."""
        row = self._conn.execute(
            """SELECT id, collection_id, title, description, collection_type,
                      template_id, output_dir, created_at, updated_at
               FROM collections
               WHERE collection_id = ?""",
            (collection_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_collection_by_title(self, title: str) -> dict | None:
        """SELECT by title."""
        row = self._conn.execute(
            """SELECT id, collection_id, title, description, collection_type,
                      template_id, output_dir, created_at, updated_at
               FROM collections
               WHERE title = ?""",
            (title,),
        ).fetchone()
        return dict(row) if row else None

    # ── collection_items CRUD ────────────────────────────────

    def delete_collection(self, collection_id: str) -> int:
        """Delete collection app records and return deleted collection rowcount."""
        self._conn.execute(
            "DELETE FROM collection_summaries WHERE collection_id = ?",
            (collection_id,),
        )
        self._conn.execute(
            "DELETE FROM collection_items WHERE collection_id = ?",
            (collection_id,),
        )
        cursor = self._conn.execute(
            "DELETE FROM collections WHERE collection_id = ?",
            (collection_id,),
        )
        return cursor.rowcount

    def insert_item(
        self,
        collection_id: str,
        job_id: str,
        item_index: int,
        title: str | None = None,
        source_uri: str | None = None,
        note_path: str | None = None,
        status: str | None = None,
        template_id: str | None = None,
    ) -> None:
        """INSERT INTO collection_items."""
        now = _now()
        self._conn.execute(
            """INSERT INTO collection_items
               (collection_id, job_id, item_index, title, source_uri,
                note_path, status, template_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (collection_id, job_id, item_index, title, source_uri,
             note_path, status, template_id, now, now),
        )

    def update_item(
        self,
        collection_id: str,
        job_id: str,
        title: str | None = None,
        source_uri: str | None = None,
        note_path: str | None = None,
        status: str | None = None,
        template_id: str | None = None,
    ) -> None:
        """UPDATE collection_items SET non-NULL fields."""
        now = _now()
        self._conn.execute(
            """UPDATE collection_items
               SET title = COALESCE(?, title),
                   source_uri = COALESCE(?, source_uri),
                   note_path = COALESCE(?, note_path),
                   status = COALESCE(?, status),
                   template_id = COALESCE(?, template_id),
                   updated_at = ?
               WHERE collection_id = ? AND job_id = ?""",
            (title, source_uri, note_path, status, template_id, now,
             collection_id, job_id),
        )

    def replace_item_job_id(
        self,
        collection_id: str,
        old_job_id: str,
        new_job_id: str,
        status: str | None = None,
    ) -> int:
        """Replace an item's placeholder job_id with the real processing job_id."""
        now = _now()
        cursor = self._conn.execute(
            """UPDATE collection_items
               SET job_id = ?,
                   status = COALESCE(?, status),
                   updated_at = ?
               WHERE collection_id = ? AND job_id = ?""",
            (new_job_id, status, now, collection_id, old_job_id),
        )
        return cursor.rowcount

    def get_item(self, collection_id: str, job_id: str) -> dict | None:
        """SELECT by collection_id AND job_id."""
        row = self._conn.execute(
            """SELECT id, collection_id, job_id, item_index, title,
                      source_uri, note_path, status, template_id,
                      created_at, updated_at
               FROM collection_items
               WHERE collection_id = ? AND job_id = ?""",
            (collection_id, job_id),
        ).fetchone()
        return dict(row) if row else None

    def get_items(self, collection_id: str) -> list[dict]:
        """SELECT all items for collection ORDER BY item_index."""
        rows = self._conn.execute(
            """SELECT id, collection_id, job_id, item_index, title,
                      source_uri, note_path, status, template_id,
                      created_at, updated_at
               FROM collection_items
               WHERE collection_id = ?
               ORDER BY item_index""",
            (collection_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_max_item_index(self, collection_id: str) -> int:
        """SELECT COALESCE(MAX(item_index), -1)."""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(item_index), -1) FROM collection_items "
            "WHERE collection_id = ?",
            (collection_id,),
        ).fetchone()
        return row[0] if row else -1

    def check_item_exists(self, collection_id: str, job_id: str) -> bool:
        """SELECT 1 WHERE collection_id=? AND job_id=?."""
        row = self._conn.execute(
            "SELECT 1 FROM collection_items "
            "WHERE collection_id = ? AND job_id = ?",
            (collection_id, job_id),
        ).fetchone()
        return row is not None