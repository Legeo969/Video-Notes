"""collection.* RPC 处理器

委托 CollectionService 提供集合 CRUD 和查询。
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
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
    job_queue: Any | None = None,
    supervisor: Any | None = None,
) -> dict[str, Any]:
    """创建 collection.* 方法处理器字典。"""

    def _service_context(params: dict[str, Any]):
        od = params.get("output_dir", output_dir)
        return _collection_service(od)

    def _collection_id(params: dict[str, Any]) -> str:
        value = str(params.get("collection_id", params.get("id", ""))).strip()
        if not value:
            raise InvalidParams("collection_id is required")
        return value

    def _item_to_dict(item) -> dict[str, Any]:
        return {
            "id": item.id,
            "job_id": item.job_id,
            "input": item.source_uri or "",
            "title": item.title,
            "source_uri": item.source_uri,
            "status": item.status or "pending",
            "note_path": item.note_path,
            "index": item.item_index,
        }

    def _source_title(source: str) -> str:
        value = source.strip()
        if not value:
            return ""
        if value.startswith(("http://", "https://")):
            return value.rstrip("/").rsplit("/", 1)[-1] or value
        return Path(value).stem or value

    def _add_sources(svc, collection_id: str, items: list[Any]) -> list[dict[str, Any]]:
        created = []
        for index, raw in enumerate(items):
            source = str(raw or "").strip()
            if not source:
                continue
            item = svc.add_job(
                collection_id,
                job_id=str(uuid.uuid4()),
                title=_source_title(source),
                source_uri=source,
                status="pending",
                item_index=None,
            )
            created.append(_item_to_dict(item))
        return created

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
        collection_id = _collection_id(params)

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
                "items": [_item_to_dict(item) for item in svc.get_items(record.collection_id)],
            }

    def handle_create(params: dict[str, Any]) -> dict[str, Any]:
        """collection.create — 创建新集合。"""
        title = str(params.get("title", params.get("name", ""))).strip()
        if not title:
            raise InvalidParams("title is required")

        with _service_context(params) as svc:
            record = svc.create_collection(
                title=title,
                collection_type=params.get("collection_type", "course"),
                description=params.get("description"),
                template_id=params.get("template_id"),
            )
            items = params.get("items") or []
            if isinstance(items, list):
                _add_sources(svc, record.collection_id, items)
            return {
                "id": record.collection_id,
                "name": record.title,
            }

    def handle_delete(params: dict[str, Any]) -> bool:
        """collection.delete — 删除集合。"""
        collection_id = _collection_id(params)

        with _service_context(params) as svc:
            return svc.delete_collection(collection_id)

    def handle_update(params: dict[str, Any]) -> dict[str, Any]:
        """collection.update — 更新集合元数据。"""
        collection_id = _collection_id(params)
        allowed = {
            "name": "title",
            "title": "title",
            "description": "description",
            "collection_type": "collection_type",
            "template_id": "template_id",
            "output_dir": "output_dir",
        }
        updates: dict[str, Any] = {}
        for key, column in allowed.items():
            if key in params:
                updates[column] = params[key]
        if "title" in updates and not str(updates["title"] or "").strip():
            raise InvalidParams("title must not be empty")
        if not updates:
            raise InvalidParams("no collection fields to update")

        with _service_context(params) as svc:
            if svc.get_collection(collection_id) is None:
                raise InternalError(f"Collection not found: {collection_id}")
            assignments = [f"{column} = ?" for column in updates]
            values = list(updates.values())
            assignments.append("updated_at = ?")
            values.append(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            values.append(collection_id)
            svc.conn.execute(
                f"""
                UPDATE collections
                SET {", ".join(assignments)}
                WHERE collection_id = ?
                """,
                values,
            )
            svc.conn.commit()
            record = svc.get_collection(collection_id)
            return {
                "id": record.collection_id,
                "name": record.title,
                "description": record.description,
                "collection_type": record.collection_type,
                "template_id": record.template_id,
                "output_dir": record.output_dir,
            }

    def handle_list_items(params: dict[str, Any]) -> list[dict[str, Any]]:
        """collection.list_items — 列出集合中的条目。"""
        collection_id = _collection_id(params)

        with _service_context(params) as svc:
            items = svc.get_items(collection_id)
            return [_item_to_dict(item) for item in items]

    def handle_add_items(params: dict[str, Any]) -> list[dict[str, Any]]:
        collection_id = _collection_id(params)
        items = params.get("items")
        if not isinstance(items, list):
            raise InvalidParams("items must be a list")
        with _service_context(params) as svc:
            return _add_sources(svc, collection_id, items)

    def handle_remove_items(params: dict[str, Any]) -> bool:
        collection_id = _collection_id(params)
        item_ids = params.get("item_ids")
        if not isinstance(item_ids, list):
            raise InvalidParams("item_ids must be a list")
        with _service_context(params) as svc:
            placeholders = ",".join("?" for _ in item_ids)
            if not placeholders:
                return True
            svc.conn.execute(
                f"""
                DELETE FROM collection_items
                WHERE collection_id = ? AND id IN ({placeholders})
                """,
                [collection_id, *item_ids],
            )
            svc.conn.commit()
            return True

    def handle_import_folder(params: dict[str, Any]) -> dict[str, Any]:
        path = str(params.get("path") or "").strip()
        if not path:
            raise InvalidParams("path is required")
        recursive = bool(params.get("recursive", False))
        sort = str(params.get("sort") or "natural")
        from src.application.services.collection_manager import CollectionManager

        manager = CollectionManager(output_dir=str(params.get("output_dir", output_dir)))
        result = manager.import_folder(path, recursive=recursive, sort=sort)
        return {
            "id": result["collection_id"],
            "collection_id": result["collection_id"],
            "count": result["count"],
        }

    def handle_export(params: dict[str, Any]) -> dict[str, Any]:
        collection_id = _collection_id(params)
        from src.application.services.collection_manager import CollectionManager

        manager = CollectionManager(output_dir=str(params.get("output_dir", output_dir)))
        result = manager.export(collection_id)
        return {
            "path": result["export_path"],
            **result,
        }

    def handle_batch_process(params: dict[str, Any]) -> dict[str, Any]:
        if supervisor is None or job_queue is None:
            raise InvalidParams("collection.batch_process requires engine task runtime")
        collection_id = _collection_id(params)
        opts = params.get("opts") or {}
        if not isinstance(opts, dict):
            raise InvalidParams("opts must be an object")

        with _service_context(params) as svc:
            items = [
                item
                for item in svc.get_items(collection_id)
                if str(item.source_uri or "").strip()
            ]
        if not items:
            raise InvalidParams("collection has no processable items")

        from src.api.handlers.process import _build_request

        requests = []
        for item in items:
            request_params = {
                **opts,
                "input": item.source_uri,
                "title": item.title,
                "collection_id": collection_id,
                "output_dir": params.get("output_dir", output_dir),
            }
            requests.append(_build_request(request_params))

        try:
            run_ids = supervisor.start_batch(requests)
        except RuntimeError as exc:
            raise InvalidParams(str(exc)) from exc

        stable_job_ids: list[str] = []
        with _service_context(params) as svc:
            for item, run_id in zip(items, run_ids):
                job = job_queue.get_job(run_id)
                if job is None or not job.job_id:
                    continue
                stable_job_ids.append(job.job_id)
                svc.replace_item_job_id(
                    collection_id,
                    item.job_id,
                    job.job_id,
                    status="pending",
                )

        batch_job_id = f"batch-{run_ids[0]}" if run_ids else "batch-empty"
        return {
            "batch_job_id": batch_job_id,
            "run_ids": run_ids,
            "job_ids": stable_job_ids,
            "count": len(run_ids),
        }

    return {
        "collection.list": handle_list,
        "collection.get": handle_get,
        "collection.create": handle_create,
        "collection.update": handle_update,
        "collection.delete": handle_delete,
        "collection.list_items": handle_list_items,
        "collection.add_items": handle_add_items,
        "collection.remove_items": handle_remove_items,
        "collection.import_folder": handle_import_folder,
        "collection.export": handle_export,
        "collection.batch_process": handle_batch_process,
    }
