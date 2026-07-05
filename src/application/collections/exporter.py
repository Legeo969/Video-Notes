"""V0.6.1 CollectionExporter — 集合结构化输出。

将 collection 的内容组织为规范的目录结构：

  output/collections/{collection_id}/
  ├── 00_课程总览.md
  ├── concept_index.md
  ├── review_questions.md
  ├── items/
  │   ├── 001_第一讲.md
  │   ├── 002_第二讲.md
  │   └── ...
  └── assets/

概览中链接到各 item 的原始 note_path，不移动原有文件。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .service import CollectionService
from .renderer import CollectionOverviewRenderer


@dataclass
class CollectionExportResult:
    """导出结果。"""

    collection_id: str
    output_dir: Path           # 导出的根目录
    overview_path: Path | None  # 00_课程总览.md
    concept_index_path: Path | None  # concept_index.md
    items_exported: int        # 已导出/链接的 item 数
    items_total: int           # 总 item 数
    errors: list[str]          # 导出过程中的错误


class CollectionExporter:
    """将 collection 导出为规范目录结构。

    Usage:
        exporter = CollectionExporter(db_conn, base_output_dir)
        result = exporter.export_all("machine-learning-course")
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        base_output_dir: str | Path = "output",
    ):
        self.conn = conn
        self.base_output_dir = Path(base_output_dir)
        self.service = CollectionService(conn)
        self.renderer = CollectionOverviewRenderer(conn)

    # ── 公共 API ──

    def export_overview(self, collection_id: str) -> Path | None:
        """生成并写入 00_课程总览.md。

        Returns:
            写入的文件 Path，或 None（集合不存在时）
        """
        collection = self.service.get_collection(collection_id)
        if collection is None:
            return None

        overview_md = self.renderer.render(collection_id)
        if overview_md is None:
            return None

        coll_dir = self._ensure_collection_dir(collection.collection_id)
        overview_path = coll_dir / "00_课程总览.md"
        overview_path.write_text(overview_md, encoding="utf-8")
        return overview_path

    def export_items_index(self, collection_id: str) -> Path | None:
        """生成 concept_index.md（跨视频概念索引）。

        Returns:
            写入的文件 Path，或 None
        """
        collection = self.service.get_collection(collection_id)
        if collection is None:
            return None

        items = self.service.get_items(collection_id)
        if not items:
            return None

        concept_index = self.renderer._build_concept_index(items)  # noqa: SLF001
        coll_dir = self._ensure_collection_dir(collection.collection_id)

        lines = [
            f"# {collection.title} — 概念索引",
            "",
            f"集合: {collection.collection_id}",
            f"类型: {collection.collection_type}",
            f"条目数: {len(items)}",
            "",
            "---",
            "",
        ]

        if concept_index:
            sorted_concepts = sorted(
                concept_index.items(),
                key=lambda kv: (-len(kv[1]), kv[0].lower()),
            )
            for concept, indices in sorted_concepts:
                lecture_refs = ", ".join(f"第 {idx + 1} 讲" for idx in sorted(indices))
                lines.append(f"- **{concept}**：{lecture_refs}")
        else:
            lines.append("暂无跨视频概念索引。")

        lines.extend([
            "",
            "---",
            "",
            f"*自动生成，数据来源：{len(items)} 个视频的知识块*",
        ])

        index_path = coll_dir / "concept_index.md"
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return index_path

    def export_all(self, collection_id: str) -> CollectionExportResult:
        """完整导出：总览 + 概念索引 + item 笔记链接。

        将每个 item 的原始笔记文件复制到 items/ 子目录，
        同时创建 assets/ 目录。
        """
        result = CollectionExportResult(
            collection_id=collection_id,
            output_dir=self._collection_dir(collection_id),
            overview_path=None,
            concept_index_path=None,
            items_exported=0,
            items_total=0,
            errors=[],
        )

        collection = self.service.get_collection(collection_id)
        if collection is None:
            result.errors.append(f"集合不存在: {collection_id}")
            return result

        # 确保目录存在
        coll_dir = self._ensure_collection_dir(collection.collection_id)
        items_dir = coll_dir / "items"
        items_dir.mkdir(exist_ok=True)
        assets_dir = coll_dir / "assets"
        assets_dir.mkdir(exist_ok=True)

        # 1. 导出总览
        try:
            result.overview_path = self.export_overview(collection_id)
        except Exception as e:
            result.errors.append(f"总览导出失败: {e}")

        # 2. 导出概念索引
        try:
            result.concept_index_path = self.export_items_index(collection_id)
        except Exception as e:
            result.errors.append(f"概念索引导出失败: {e}")

        # 3. 复制各 item 的笔记到 items/
        items = self.service.get_items(collection_id)
        result.items_total = len(items)
        for item in items:
            try:
                self._link_item_note(item, items_dir)
                result.items_exported += 1
            except Exception as e:
                result.errors.append(
                    f"Item #{item.item_index + 1} ({item.job_id}) 导出失败: {e}"
                )

        # 4. 写入 .collection_meta.json
        status = self.service.get_status(collection_id)
        meta = {
            "collection_id": collection.collection_id,
            "title": collection.title,
            "type": collection.collection_type,
            "template_id": collection.template_id,
            "total_items": result.items_total,
            "completed": status.completed if status else 0,
            "failed": status.failed if status else 0,
            "citation_ready": status.citation_ready if status else 0,
            "template_warnings": status.template_warnings if status else 0,
            "exported_at": _utcnow(),
        }
        meta_path = coll_dir / ".collection_meta.json"
        import json
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return result

    # ── 内部方法 ──

    def _collection_dir(self, collection_id: str) -> Path:
        """获取 collection 输出目录路径（不创建）。"""
        return self.base_output_dir / "collections" / collection_id

    def _ensure_collection_dir(self, collection_id: str) -> Path:
        """获取并创建 collection 输出目录。"""
        coll_dir = self._collection_dir(collection_id)
        coll_dir.mkdir(parents=True, exist_ok=True)
        return coll_dir

    def _link_item_note(self, item, items_dir: Path) -> None:
        """将 item 的原始笔记复制到 items/ 子目录。

        Args:
            item: CollectionItem 实例
            items_dir: items/ 目录 Path
        """
        from collections.abc import Iterable

        if not hasattr(item, 'note_path') or not item.note_path:
            return

        note_path = Path(item.note_path)
        if not note_path.exists():
            return

        # 生成格式化文件名：{序号:03d}_{标题}.md
        index_str = f"{item.item_index + 1:03d}"
        safe_title = _safe_filename(item.title or f"item_{item.job_id}")
        dest_name = f"{index_str}_{safe_title}.md"
        dest_path = items_dir / dest_name

        # 复制文件
        shutil.copy2(str(note_path), str(dest_path))

        # 也尝试复制对应的 assets 目录
        note_parent = note_path.parent
        possible_assets = [
            note_parent / "assets",
            note_parent / "frames",
        ]
        for asset_dir in possible_assets:
            if isinstance(asset_dir, Iterable) and not isinstance(asset_dir, (str, Path)):
                continue
            if not isinstance(asset_dir, Path):
                asset_dir = Path(str(asset_dir))
            if asset_dir.is_dir():
                dest_asset_dir = items_dir / f"{index_str}_assets"
                if not dest_asset_dir.exists():
                    shutil.copytree(str(asset_dir), str(dest_asset_dir))


def _safe_filename(name: str, max_len: int = 60) -> str:
    """将字符串转换为安全的文件名。"""
    # 替换不安全字符
    safe = name
    for ch in r'<>:"/\|?*':
        safe = safe.replace(ch, "_")
    # 截断长度
    if len(safe) > max_len:
        safe = safe[:max_len - 3] + "..."
    # 去除首尾空格和点
    safe = safe.strip(". ")
    return safe or "untitled"


def _utcnow() -> str:
    """ISO 8601 UTC 时间戳。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
