"""CollectionManager — CLI 兼容的视频集合管理封装。

封装 src.application.collections 中的现有服务 (CollectionService,
CollectionFolderImporter, CollectionPlaylistImporter, CollectionExporter)，
提供一个自包含的简化接口供 CLI 命令使用。

每个公共方法独立管理数据库连接，无需外部传入 conn。
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

from src.application.collections.exporter import CollectionExporter
from src.application.collections.importer import (
    CollectionFolderImporter,
    CollectionPlaylistImporter,
    SortMode,
)
from src.application.collections.service import CollectionService
from src.application.services.job_queue import get_default_db_path
from src.db.database import initialize_database


class CollectionManager:
    """视频集合管理器 — CLI 友好的封装。

    Usage:
        manager = CollectionManager(output_dir="./output")
        result = manager.create("机器学习基础", collection_type="course")
        collections = manager.list_collections()
        status = manager.get_status("machine-learning-basics")
    """

    def __init__(self, output_dir: str = "./output"):
        self.output_dir = output_dir
        self.db_path = get_default_db_path(output_dir)
        initialize_database(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ── 集合 CRUD ────────────────────────────────────────────────

    def create(
        self,
        title: str,
        collection_type: str = "course",
        template_id: str | None = None,
    ) -> dict:
        """创建新集合并返回字典表示。

        Args:
            title: 集合显示名称。
            collection_type: 类型 (course|playlist|folder|project)。
            template_id: 默认笔记模板 ID。

        Returns:
            新创建的集合信息字典。
        """
        with self._connect() as conn:
            record = CollectionService(conn).create_collection(
                title=title,
                collection_type=collection_type,
                template_id=template_id,
                output_dir=self.output_dir,
            )
            return _collection_to_dict(record)

    def list_collections(self) -> list[dict]:
        """列出所有集合。

        Returns:
            集合信息字典列表，按创建时间降序。
        """
        with self._connect() as conn:
            records = CollectionService(conn).list_collections()
            return [_collection_to_dict(r) for r in records]

    def get_status(self, identifier: str) -> dict | None:
        """获取集合聚合状态。

        Args:
            identifier: collection_id 或 title。

        Returns:
            状态字典或 None（集合不存在时）。
        """
        with self._connect() as conn:
            service = CollectionService(conn)
            collection = service.get_collection(identifier)
            if collection is None:
                return None
            status = service.get_status(collection.collection_id)
            total = status.total_items if status else 0
            completed = status.completed if status else 0
            return {
                "collection_id": collection.collection_id,
                "title": collection.title,
                "type": collection.collection_type,
                "total_jobs": total,
                "completed_jobs": completed,
                "completion_rate": completed / total if total else 0.0,
            }

    def add_job(self, collection_id: str, run_id: int) -> dict:
        """将已存在的任务添加到集合。

        Args:
            collection_id: 目标集合 slug。
            run_id: processing_runs 表的主键 ID。

        Returns:
            新添加的条目信息字典。

        Raises:
            ValueError: 如果 run_id 对应的任务不存在。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT job_id, input_path, title, output_path, status "
                "FROM processing_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"任务不存在: {run_id}")
            item = CollectionService(conn).add_job(
                collection_id=collection_id,
                job_id=row["job_id"],
                title=row["title"],
                source_uri=row["input_path"],
                note_path=row["output_path"],
                status=row["status"],
            )
            return _item_to_dict(item)

    def generate_overview(self, identifier: str) -> str | None:
        """生成集合总览 Markdown。

        Args:
            identifier: collection_id 或 title。

        Returns:
            Markdown 字符串或 None（集合不存在时）。
        """
        with self._connect() as conn:
            service = CollectionService(conn)
            collection = service.get_collection(identifier)
            if collection is None:
                return None
            return service.generate_overview(collection.collection_id)

    # ── 导入 ─────────────────────────────────────────────────────

    def import_folder(
        self,
        folder_path: str,
        collection_id: str | None = None,
        collection_type: str = "course",
        template_id: str | None = None,
        recursive: bool = False,
        sort: SortMode = "natural",
    ) -> dict:
        """扫描文件夹导入媒体文件为集合条目。

        Args:
            folder_path: 目标文件夹路径。
            collection_id: 可选 — 指定集合 slug，未指定时用文件夹名。
            collection_type: 集合类型。
            template_id: 默认笔记模板 ID。
            recursive: 是否递归扫描子文件夹。
            sort: 排序方式 (name|mtime|natural)。

        Returns:
            包含 collection_id 和导入计数的字典。
        """
        importer = CollectionFolderImporter()
        imported = importer.import_folder(folder_path, recursive=recursive, sort=sort)
        title = collection_id or Path(folder_path).name
        return self._import_items(
            imported,
            collection_id=collection_id,
            title=title,
            collection_type=collection_type,
            template_id=template_id,
        )

    def import_playlist(
        self,
        playlist_url: str,
        collection_id: str | None = None,
        collection_type: str = "course",
        template_id: str | None = None,
    ) -> dict:
        """展开 playlist URL 导入为集合条目。

        Args:
            playlist_url: YouTube/B站等 playlist URL。
            collection_id: 可选 — 指定集合 slug。
            collection_type: 集合类型。
            template_id: 默认笔记模板 ID。

        Returns:
            包含 collection_id 和导入计数的字典。
        """
        importer = CollectionPlaylistImporter()
        imported = importer.import_playlist(playlist_url)
        title = collection_id or "playlist"
        return self._import_items(
            imported,
            collection_id=collection_id,
            title=title,
            collection_type=collection_type,
            template_id=template_id,
        )

    # ── 导出 ─────────────────────────────────────────────────────

    def export(self, identifier: str) -> dict:
        """完整导出集合：总览 + 概念索引 + item 笔记。

        Args:
            identifier: collection_id 或 title。

        Returns:
            导出结果字典。

        Raises:
            ValueError: 如果集合不存在。
        """
        with self._connect() as conn:
            service = CollectionService(conn)
            collection = service.get_collection(identifier)
            if collection is None:
                raise ValueError(f"集合不存在: {identifier}")
            result = CollectionExporter(conn, self.output_dir).export_all(
                collection.collection_id
            )
            return {
                "collection_id": result.collection_id,
                "export_path": str(result.output_dir),
                "items_exported": result.items_exported,
                "items_total": result.items_total,
                "errors": list(result.errors),
            }

    # ── 内部帮助方法 ─────────────────────────────────────────────

    def _import_items(
        self,
        imported: list,
        *,
        collection_id: str | None,
        title: str,
        collection_type: str,
        template_id: str | None,
    ) -> dict:
        """将 ImportItem 列表持久化到集合。

        如果指定的 collection_id 不存在则自动创建。

        Args:
            imported: ImportItem 对象列表。
            collection_id: 可选集合 slug。
            title: 集合标题（新建时使用）。
            collection_type: 集合类型。
            template_id: 默认模板 ID。

        Returns:
            包含 collection_id 和导入计数的字典。
        """
        with self._connect() as conn:
            service = CollectionService(conn)

            collection = (
                service.get_collection(collection_id) if collection_id else None
            )
            if collection is None:
                collection = service.create_collection(
                    title=title,
                    collection_type=collection_type,
                    template_id=template_id,
                    output_dir=self.output_dir,
                    collection_id=collection_id,
                )

            for item in imported:
                service.add_job(
                    collection.collection_id,
                    job_id=str(uuid.uuid4()),
                    item_index=item.index,
                    title=item.title,
                    source_uri=item.path_or_url,
                    status="pending",
                    template_id=template_id,
                )

            return {
                "collection_id": collection.collection_id,
                "count": len(imported),
            }


# ── 转换辅助 ─────────────────────────────────────────────────────


def _collection_to_dict(record) -> dict:
    """将 CollectionRecord 转换为可序列化字典。"""
    return {
        "collection_id": record.collection_id,
        "title": record.title,
        "description": record.description or "",
        "type": record.collection_type,
        "template_id": record.template_id,
        "output_dir": record.output_dir,
    }


def _item_to_dict(item) -> dict:
    """将 CollectionItem 转换为可序列化字典。"""
    return {
        "collection_id": item.collection_id,
        "job_id": item.job_id,
        "title": item.title,
        "source_uri": item.source_uri,
        "note_path": item.note_path,
        "status": item.status,
    }
