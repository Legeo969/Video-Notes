"""collection.* RPC 处理器

委托 CollectionService 提供集合 CRUD 和查询。
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

from src.api.protocol.errors import InternalError, InvalidParams

logger = logging.getLogger(__name__)


@contextmanager
def _collection_service(output_dir: str = "./output") -> Iterator[Any]:
    """Yield a CollectionService with a live, correctly-owned DB connection.

    Do not call ``gateway.connection().__enter__()`` on a temporary context
    manager.  On CPython that temporary may be finalized immediately, which
    runs ``__exit__`` and closes the SQLite connection before the handler uses
    it (``Cannot operate on a closed database``).
    """
    from src.application.services.job_queue import get_default_db_path
    from src.infrastructure.db.gateway import DatabaseGateway
    from src.application.collections.service import CollectionService

    db_path = get_default_db_path(output_dir)
    gateway = DatabaseGateway(db_path)
    gateway.initialize()
    with gateway.connection() as conn:
        yield CollectionService(conn)


def create_collections_handlers(
    output_dir: str = "./output",
) -> dict[str, Any]:
    """创建 collection.* 方法处理器字典。"""

    def _service_context(params: dict[str, Any]):
        od = params.get("output_dir", output_dir)
        return _collection_service(od)

    def handle_list(params: dict[str, Any]) -> list[dict[str, Any]]:
        """collection.list — 列出所有集合。"""
        with _service_context(params) as svc:
            records = svc.list_collections()
            return [
                {
                    "id": r.collection_id,
                    "name": r.title,
                    "item_count": len(svc.get_items(r.collection_id)),
                    "status": "active",
                }
                for r in records
            ]

    def handle_get(params: dict[str, Any]) -> dict[str, Any]:
        """collection.get — 获取集合详情。"""
        collection_id = params.get("collection_id", "").strip()
        if not collection_id:
            raise InvalidParams("collection_id is required")

        with _service_context(params) as svc:
            record = svc.get_collection(collection_id)
            if record is None:
                raise InternalError(f"Collection not found: {collection_id}")

            status = svc.get_status(collection_id)
            return {
                "id": record.collection_id,
                "name": record.title,
                "description": record.description,
                "collection_type": record.collection_type,
                "item_count": status.total_items if status else 0,
                "completed": status.completed if status else 0,
                "failed": status.failed if status else 0,
                "pending": status.pending if status else 0,
                "status": "active",
                "created_at": record.created_at,
            }

    def handle_create(params: dict[str, Any]) -> dict[str, Any]:
        """collection.create — 创建新集合。"""
        title = params.get("title", "").strip()
        if not title:
            raise InvalidParams("title is required")

        with _service_context(params) as svc:
            record = svc.create_collection(
                title=title,
                collection_type=params.get("collection_type", "course"),
                description=params.get("description"),
                template_id=params.get("template_id"),
            )
            return {
                "id": record.collection_id,
                "name": record.title,
            }

    def handle_delete(params: dict[str, Any]) -> bool:
        """collection.delete — 删除集合。"""
        collection_id = params.get("collection_id", "").strip()
        if not collection_id:
            raise InvalidParams("collection_id is required")

        with _service_context(params) as svc:
            return svc.delete_collection(collection_id)

    def handle_list_items(params: dict[str, Any]) -> list[dict[str, Any]]:
        """collection.list_items — 列出集合中的条目。"""
        collection_id = params.get("collection_id", "").strip()
        if not collection_id:
            raise InvalidParams("collection_id is required")

        with _service_context(params) as svc:
            items = svc.get_items(collection_id)
            return [
                {
                    "id": item.job_id,
                    "title": item.title,
                    "source_uri": item.source_uri,
                    "status": item.status,
                    "note_path": item.note_path,
                    "index": item.item_index,
                }
                for item in items
            ]

    return {
        "collection.list": handle_list,
        "collection.get": handle_get,
        "collection.create": handle_create,
        "collection.delete": handle_delete,
        "collection.list_items": handle_list_items,
    }
