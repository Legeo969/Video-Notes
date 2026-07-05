"""notes.* RPC 处理器

委托 NoteRepository 和文件系统提供笔记查询能力。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from src.api.protocol.errors import InternalError, InvalidParams

logger = logging.getLogger(__name__)


def create_notes_handlers(
    db_path: str | None = None,
    output_dir: str = "./output",
) -> dict[str, Any]:
    """创建 notes.* 方法处理器字典。

    Args:
        db_path: 数据库路径。为 None 时自动从 output_dir 推导。
        output_dir: 输出根目录。
    """

    if db_path is None:
        from src.application.services.job_queue import get_default_db_path
        db_path = get_default_db_path(output_dir)

    def _get_connection():
        """获取数据库连接（延迟初始化）。"""
        import sqlite3
        # 使用默认 gateway 简化
        from src.infrastructure.db.gateway import DatabaseGateway
        gateway = DatabaseGateway(db_path)
        gateway.initialize()
        return gateway.connection().__enter__()

    def handle_list(params: dict[str, Any]) -> list[dict[str, Any]]:
        """notes.list — 列出所有笔记。"""
        limit = params.get("limit", 50)
        offset = params.get("offset", 0)
        try:
            conn = _get_connection()
            try:
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
            finally:
                conn.close()
        except Exception as e:
            logger.exception("Failed to list notes")
            raise InternalError(str(e))

    def handle_get(params: dict[str, Any]) -> dict[str, Any]:
        """notes.get — 获取笔记完整内容。"""
        note_id = params.get("note_id")
        if note_id is None:
            raise InvalidParams("note_id is required")
        try:
            note_id = int(note_id)
        except (ValueError, TypeError):
            raise InvalidParams("note_id must be an integer")

        try:
            conn = _get_connection()
            try:
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
            finally:
                conn.close()
        except InternalError:
            raise
        except Exception as e:
            logger.exception("Failed to get note")
            raise InternalError(str(e))

    def handle_get_by_path(params: dict[str, Any]) -> dict[str, Any]:
        """notes.get_by_path — 按路径获取笔记内容。"""
        path = params.get("path", "").strip()
        if not path:
            raise InvalidParams("path is required")

        try:
            conn = _get_connection()
            try:
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
            finally:
                conn.close()
        except InternalError:
            raise
        except Exception as e:
            logger.exception("Failed to get note by path")
            raise InternalError(str(e))

    return {
        "notes.list": handle_list,
        "notes.get": handle_get,
        "notes.get_by_path": handle_get_by_path,
    }
