"""notes.* RPC handlers.

All SQLite connections are owned by a context manager for the full request.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
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

    def handle_get(params: dict[str, Any]) -> dict[str, Any]:
        note_id = params.get("note_id", params.get("id"))
        if note_id is None:
            raise InvalidParams("note_id is required")
        try:
            note_id = int(note_id)
        except (ValueError, TypeError) as exc:
            raise InvalidParams("note_id must be an integer") from exc

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

    return {
        "notes.list": handle_list,
        "notes.get": handle_get,
        "notes.get_by_path": handle_get_by_path,
    }
