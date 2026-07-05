"""NoteRepository — CRUD for the notes table."""
from __future__ import annotations

import sqlite3


class NoteRepository:
    """Encapsulates all SQL access for the notes + note_keywords tables.

    Does NOT manage transactions — the caller (e.g. DatabaseGateway
    connection context manager) is responsible for commit/rollback.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(
        self,
        rel_path: str,
        title: str = "",
        content: str = "",
        keywords: list[str] | None = None,
    ) -> int:
        """Insert or update a note by rel_path.  Returns the note id.

        If *keywords* is provided, deletes all existing keywords for this
        note and inserts the given list.
        """
        self._conn.execute(
            """
            INSERT INTO notes (rel_path, title, content)
            VALUES (?, ?, ?)
            ON CONFLICT(rel_path) DO UPDATE SET
                title      = excluded.title,
                content    = excluded.content,
                updated_at = CURRENT_TIMESTAMP
            """,
            (rel_path, title, content),
        )
        row = self._conn.execute(
            "SELECT id FROM notes WHERE rel_path = ?", (rel_path,)
        ).fetchone()
        note_id = int(row["id"])

        if keywords is not None:
            self._conn.execute(
                "DELETE FROM note_keywords WHERE note_id = ?", (note_id,)
            )
            for kw in keywords:
                self._conn.execute(
                    "INSERT OR IGNORE INTO note_keywords (note_id, keyword) VALUES (?, ?)",
                    (note_id, kw),
                )

        return note_id

    def get_by_id(self, note_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_by_rel_path(self, rel_path: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM notes WHERE rel_path = ?", (rel_path,)
        ).fetchone()
        return dict(row) if row else None

    def search(self, query: str, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT * FROM notes
            WHERE title LIKE ? OR content LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, note_id: int) -> None:
        self._conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))