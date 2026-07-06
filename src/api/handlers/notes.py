"""notes.* RPC handlers.

All SQLite connections are owned by a context manager for the full request.
"""

from __future__ import annotations

import logging
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src.api.protocol.errors import InternalError, InvalidParams

logger = logging.getLogger(__name__)


def create_notes_handlers(
    db_path: str | None = None,
    output_dir: str = "./output",
) -> dict[str, Any]:
    """Create the ``notes.*`` RPC handlers."""

    if db_path is None:
        from src.application.services.job_queue import get_default_db_path

        db_path = get_default_db_path(output_dir)

    @contextmanager
    def _connection() -> Iterator[Any]:
        """Yield a live connection and close it only after the request ends."""
        from src.infrastructure.db.gateway import DatabaseGateway

        gateway = DatabaseGateway(db_path)
        gateway.initialize()
        with gateway.connection() as conn:
            yield conn

    def handle_list(params: dict[str, Any]) -> list[dict[str, Any]]:
        limit = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
        try:
            with _connection() as conn:
                rows = conn.execute(
                    "SELECT id, title, rel_path, created_at FROM notes "
                    "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                return [
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "path": row["rel_path"],
                        "created_at": row["created_at"],
                    }
                    for row in rows
                ]
        except Exception as exc:
            logger.exception("Failed to list notes")
            raise InternalError(str(exc)) from exc

    def _note_id(params: dict[str, Any]) -> int:
        note_id = params.get("note_id", params.get("id"))
        if note_id is None:
            raise InvalidParams("note_id is required")
        try:
            return int(note_id)
        except (ValueError, TypeError) as exc:
            raise InvalidParams("note_id must be an integer") from exc

    def _note_path(rel_path: str) -> Path:
        path = Path(rel_path)
        if path.is_absolute():
            return path
        return Path(output_dir).expanduser().resolve() / path

    def handle_get(params: dict[str, Any]) -> dict[str, Any]:
        note_id = _note_id(params)

        try:
            with _connection() as conn:
                row = conn.execute(
                    "SELECT id, title, content, rel_path FROM notes WHERE id = ?",
                    (note_id,),
                ).fetchone()
                if row is None:
                    raise InternalError(f"Note not found: {note_id}")
                return {
                    "id": row["id"],
                    "title": row["title"],
                    "content": row["content"],
                    "path": row["rel_path"],
                }
        except InternalError:
            raise
        except Exception as exc:
            logger.exception("Failed to get note")
            raise InternalError(str(exc)) from exc

    def handle_get_by_path(params: dict[str, Any]) -> dict[str, Any]:
        path = str(params.get("path", "")).strip()
        if not path:
            raise InvalidParams("path is required")

        try:
            with _connection() as conn:
                row = conn.execute(
                    "SELECT id, title, content, rel_path FROM notes WHERE rel_path = ?",
                    (path,),
                ).fetchone()
                if row is None:
                    raise InternalError(f"Note not found: {path}")
                return {
                    "id": row["id"],
                    "title": row["title"],
                    "content": row["content"],
                    "path": row["rel_path"],
                }
        except InternalError:
            raise
        except Exception as exc:
            logger.exception("Failed to get note by path")
            raise InternalError(str(exc)) from exc

    def handle_search(params: dict[str, Any]) -> list[dict[str, Any]]:
        query = str(params.get("query") or "").strip()
        if not query:
            return []
        try:
            limit = max(1, min(200, int(params.get("limit", 50))))
        except (ValueError, TypeError) as exc:
            raise InvalidParams("limit must be an integer") from exc
        try:
            with _connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id, title, rel_path, created_at
                    FROM notes
                    WHERE title LIKE ? OR content LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (f"%{query}%", f"%{query}%", limit),
                ).fetchall()
                return [
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "path": row["rel_path"],
                        "created_at": row["created_at"],
                    }
                    for row in rows
                ]
        except Exception as exc:
            logger.exception("Failed to search notes")
            raise InternalError(str(exc)) from exc

    def handle_update(params: dict[str, Any]) -> bool:
        note_id = _note_id(params)
        content = str(params.get("content") or "")
        title = params.get("title")
        try:
            with _connection() as conn:
                row = conn.execute(
                    "SELECT title FROM notes WHERE id = ?",
                    (note_id,),
                ).fetchone()
                if row is None:
                    raise InternalError(f"Note not found: {note_id}")
                conn.execute(
                    """
                    UPDATE notes
                    SET title = ?, content = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (str(title) if title is not None else row["title"], content, note_id),
                )
                return True
        except InternalError:
            raise
        except Exception as exc:
            logger.exception("Failed to update note")
            raise InternalError(str(exc)) from exc

    def handle_delete(params: dict[str, Any]) -> bool:
        note_id = _note_id(params)
        try:
            with _connection() as conn:
                cursor = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
                return cursor.rowcount > 0
        except Exception as exc:
            logger.exception("Failed to delete note")
            raise InternalError(str(exc)) from exc

    def _get_existing_note_path(params: dict[str, Any]) -> str:
        note_id = _note_id(params)
        with _connection() as conn:
            row = conn.execute(
                "SELECT rel_path FROM notes WHERE id = ?",
                (note_id,),
            ).fetchone()
            if row is None:
                raise InternalError(f"Note not found: {note_id}")
            return str(_note_path(row["rel_path"]))

    def handle_open(params: dict[str, Any]) -> str:
        path = _get_existing_note_path(params)
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.Popen(["open", path])
        except OSError as exc:
            raise InternalError(str(exc)) from exc
        return path

    def handle_reveal(params: dict[str, Any]) -> str:
        path = _get_existing_note_path(params)
        try:
            os.startfile(str(Path(path).parent))  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.Popen(["open", str(Path(path).parent)])
        except OSError as exc:
            raise InternalError(str(exc)) from exc
        return path

    return {
        "notes.list": handle_list,
        "notes.get": handle_get,
        "notes.get_by_path": handle_get_by_path,
        "notes.search": handle_search,
        "notes.update": handle_update,
        "notes.delete": handle_delete,
        "notes.open": handle_open,
        "notes.reveal": handle_reveal,
    }
