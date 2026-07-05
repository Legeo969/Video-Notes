"""V0.6 CollectionService — 集合 CRUD + 状态聚合。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone

from src.infrastructure.db.repositories.collection_repository import CollectionRepository

from .models import CollectionItem, CollectionRecord, CollectionStatus


# ── Slug 生成 ───────────────────────────────────────────────

def generate_collection_id(title: str) -> str:
    """从标题生成稳定 slug。

    - ASCII 标题：lowercase + 连字符，最大 64 字符
    - 非 ASCII 标题（中文等）："col-" + MD5 前 8 位
    """
    if title.isascii():
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        if slug:
            return slug[:64]
    h = hashlib.md5(title.encode("utf-8")).hexdigest()[:8]
    return f"col-{h}"


# ── CollectionService ───────────────────────────────────────

class CollectionService:
    """视频集合管理服务。

    所有方法直接使用 sqlite3.Connection，由调用方管理事务。
    """

    def __init__(self, conn):
        self.conn = conn
        self._repo = CollectionRepository(conn)

    # ── CRUD ─────────────────────────────────────────────

    def create_collection(
        self,
        title: str,
        collection_type: str = "course",
        description: str | None = None,
        template_id: str | None = None,
        output_dir: str | None = None,
        collection_id: str | None = None,
    ) -> CollectionRecord:
        """创建新集合。

        Args:
            title: 显示名称
            collection_type: course | playlist | folder | project
            description: 可选描述
            template_id: 默认模板 ID
            output_dir: 集合输出目录
            collection_id: 自定义 slug（默认自动生成）

        Returns:
            新创建的 CollectionRecord

        Raises:
            ValueError: 如果 collection_id 已存在
        """
        now = _utcnow()
        slug = collection_id or generate_collection_id(title)

        try:
            self._repo.insert_collection(
                slug, title, collection_type=collection_type,
                description=description, template_id=template_id,
                output_dir=output_dir,
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise ValueError(f"集合已存在: {slug}")

        return self.get_collection(slug)

    def list_collections(self) -> list[CollectionRecord]:
        """列出所有集合，按创建时间降序。"""
        rows = self._repo.list_collections()
        return [_row_to_collection(r) for r in rows]

    def get_collection(self, identifier: str) -> CollectionRecord | None:
        """通过 collection_id 或 title 查找集合。

        Args:
            identifier: collection_id 或 title

        Returns:
            CollectionRecord 或 None
        """
        # 先按 collection_id 精确匹配
        row = self._repo.get_collection_by_id(identifier)

        if row is None:
            # 再按 title 匹配
            row = self._repo.get_collection_by_title(identifier)

        return _row_to_collection(row) if row else None

    # ── Item 管理 ────────────────────────────────────────

    def delete_collection(self, collection_id: str) -> bool:
        """Delete collection app records without deleting source media files."""
        try:
            deleted = self._repo.delete_collection(collection_id)
            self.conn.commit()
            return deleted > 0
        except Exception:
            self.conn.rollback()
            raise

    def add_job(
        self,
        collection_id: str,
        job_id: str,
        item_index: int | None = None,
        title: str | None = None,
        source_uri: str | None = None,
        note_path: str | None = None,
        status: str | None = None,
        template_id: str | None = None,
    ) -> CollectionItem:
        """将 job 加入集合（幂等）。

        如果 (collection_id, job_id) 已存在，更新元数据但不改变 item_index。
        如果 item_index 未指定，自动分配为当前最大 index + 1。

        Args:
            collection_id: 集合 slug
            job_id: 任务 ID（来自 processing_runs.job_id）
            item_index: 序号（可选，默认自动分配）
            title: 任务标题
            source_uri: 来源 URL/路径
            note_path: 笔记文件路径
            status: 任务状态
            template_id: 使用的模板 ID

        Returns:
            CollectionItem
        """
        now = _utcnow()

        # 检查是否已存在
        existing = self._repo.get_item(collection_id, job_id)

        if existing:
            # 幂等：更新元数据
            self._repo.update_item(
                collection_id, job_id, title=title, source_uri=source_uri,
                note_path=note_path, status=status, template_id=template_id,
            )
            self.conn.commit()
            return self._get_item(collection_id, job_id)

        # 新条目：自动分配 item_index
        if item_index is None:
            max_idx = self._repo.get_max_item_index(collection_id)
            item_index = max_idx + 1

        self._repo.insert_item(
            collection_id, job_id, item_index, title=title,
            source_uri=source_uri, note_path=note_path, status=status,
            template_id=template_id,
        )
        self.conn.commit()
        return self._get_item(collection_id, job_id)

    def get_items(self, collection_id: str) -> list[CollectionItem]:
        """获取集合中所有条目，按 item_index 排序。"""
        rows = self._repo.get_items(collection_id)
        return [_row_to_item(r) for r in rows]

    def replace_item_job_id(
        self,
        collection_id: str,
        old_job_id: str,
        new_job_id: str,
        status: str | None = None,
    ) -> CollectionItem:
        """将导入占位 job_id 替换为真实处理任务 job_id。"""
        if old_job_id == new_job_id:
            if status is not None:
                self._repo.update_item(collection_id, new_job_id, status=status)
                self.conn.commit()
            return self._get_item(collection_id, new_job_id)

        try:
            updated = self._repo.replace_item_job_id(
                collection_id, old_job_id, new_job_id, status=status
            )
            if updated == 0:
                raise ValueError(f"条目不存在: {collection_id}/{old_job_id}")
            self.conn.commit()
            return self._get_item(collection_id, new_job_id)
        except Exception:
            self.conn.rollback()
            raise

    def _get_item(self, collection_id: str, job_id: str) -> CollectionItem:
        """获取单个条目（内部用）。"""
        row = self._repo.get_item(collection_id, job_id)
        if row is None:
            raise ValueError(f"条目不存在: {collection_id}/{job_id}")
        return _row_to_item(row)

    # ── 状态聚合 ─────────────────────────────────────────

    def get_status(self, collection_id: str) -> CollectionStatus | None:
        """获取集合的聚合状态。

        从 processing_runs + collection_items 交叉查询：
        - 各状态 count
        - citation 就绪数
        - template warning 数

        Returns:
            CollectionStatus 或 None（集合不存在时）
        """
        coll = self.get_collection(collection_id)
        if coll is None:
            return None

        items = self.get_items(collection_id)
        status = CollectionStatus(collection=coll)

        if not items:
            return status

        status.total_items = len(items)
        job_ids = [it.job_id for it in items]

        # 从 processing_runs 批量获取状态
        try:
            placeholders = ",".join("?" * len(job_ids))
            rows = self.conn.execute(
                f"""
                SELECT job_id, status, input_path
                FROM processing_runs
                WHERE job_id IN ({placeholders})
                """,
                job_ids,
            ).fetchall()
        except Exception:
            # processing_runs 表可能不存在（测试/独立 DB）
            rows = []

        # 构建 job_id → (status, input_path) 映射
        run_map: dict[str, tuple[str, str]] = {}
        for r in rows:
            run_map[r[0]] = (r[1], r[2])

        for item in items:
            if item.job_id in run_map:
                run_status, _source = run_map[item.job_id]
                if run_status == "completed":
                    status.completed += 1
                elif run_status == "failed":
                    status.failed += 1
                elif run_status == "cancelled":
                    status.cancelled += 1
                elif run_status == "paused":
                    status.paused += 1
                elif run_status == "pending":
                    status.pending += 1
                else:
                    status.processing += 1
            else:
                status.pending += 1

            # 检查 provenance / citations
            pv = self._check_provenance(item.job_id)
            if pv.get("is_citation_ready"):
                status.citation_ready += 1

            # 检查 template validation
            if self._has_template_warnings(item.job_id):
                status.template_warnings += 1

        return status

    def _check_provenance(self, job_id: str) -> dict:
        """检查 job 的 provenance 状态。"""
        try:
            from src.application.provenance.indexer import ProvenanceIndexer
            row = self.conn.execute("PRAGMA database_list").fetchone()
            db_path = str(row[2]) if row and row[2] else ""
            if not db_path:
                return {"indexed": False, "is_citation_ready": False}
            indexer = ProvenanceIndexer(db_path)
            return indexer.check_provenance_status(job_id)
        except Exception:
            return {"indexed": False, "is_citation_ready": False}

    def _has_template_warnings(self, job_id: str) -> bool:
        """检查 job 是否有 template validation warnings。

        读取 artifacts/template_validation.json（如果存在）。
        """
        # 从 processing_runs 获取 job_dir
        try:
            row = self.conn.execute(
                "SELECT job_dir FROM processing_runs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        except Exception:
            return False  # 表不存在
        if row is None or not row[0]:
            return False

        validation_path = os.path.join(
            str(row[0]), "artifacts", "template_validation.json"
        )
        try:
            with open(validation_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            warnings = data.get("warnings", [])
            return len(warnings) > 0
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return False

    # ── 总览 ─────────────────────────────────────────────

    def generate_overview(self, collection_id: str) -> str | None:
        """生成集合总览 Markdown。

        Returns:
            Markdown 字符串或 None（集合不存在时）
        """
        from .renderer import CollectionOverviewRenderer

        renderer = CollectionOverviewRenderer(self.conn)
        return renderer.render(collection_id)


# ── 行转换辅助 ──────────────────────────────────────────────

def _row_to_collection(row) -> CollectionRecord:
    return CollectionRecord(
        id=row["id"],
        collection_id=row["collection_id"],
        title=row["title"],
        description=row["description"],
        collection_type=row["collection_type"],
        template_id=row["template_id"],
        output_dir=row["output_dir"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_item(row) -> CollectionItem:
    return CollectionItem(
        id=row["id"],
        collection_id=row["collection_id"],
        job_id=row["job_id"],
        item_index=row["item_index"],
        title=row["title"],
        source_uri=row["source_uri"],
        note_path=row["note_path"],
        status=row["status"],
        template_id=row["template_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
