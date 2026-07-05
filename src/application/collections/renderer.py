"""V0.6 Collection Overview Renderer — 确定性聚合生成课程总览。

第一版不使用 LLM，纯数据聚合：
- 集合基本信息
- 视频列表（含状态）
- 每节摘要（从 notes.md heading + knowledge_blocks + 时长）
- 关键概念索引（跨视频）
- 质量警告
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict
from typing import Any

from .models import CollectionItem, CollectionRecord
from .service import CollectionService


# ── Heading 提取 ────────────────────────────────────────────

_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


def _extract_headings(md_path: str) -> list[str]:
    """从 Markdown 文件中提取前 10 个 heading。"""
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read(1024 * 64)  # 只读前 64KB
    except (FileNotFoundError, OSError):
        return []
    return [m.strip() for m in _HEADING_RE.findall(content)][:10]


# ── CollectionOverviewRenderer ───────────────────────────────

class CollectionOverviewRenderer:
    """从数据库和文件系统生成集合总览 Markdown。

    数据来源：
    - collections / collection_items 表
    - processing_runs 表（状态、时长、job_dir）
    - knowledge_blocks 表（关键概念）
    - artifacts/notes.md（heading 摘要）
    - artifacts/template_validation.json（质量警告）
    """

    def __init__(self, conn: sqlite3.Connection, db_path: str | None = None):
        self.conn = conn
        self._db_path = db_path
        self.service = CollectionService(conn)

    def render(self, collection_id: str) -> str | None:
        """生成集合总览 Markdown。

        Returns:
            Markdown 字符串或 None（集合不存在时）
        """
        coll = self.service.get_collection(collection_id)
        if coll is None:
            return None

        items = self.service.get_items(collection_id)
        status = self.service.get_status(collection_id)
        if status is None:
            return None

        lines: list[str] = []

        # ── Header ──
        lines.append(f"# {coll.title}")
        lines.append("")
        lines.append(f"**类型**：{coll.collection_type}  ")
        if coll.description:
            lines.append(f"**描述**：{coll.description}  ")
        lines.append(f"**视频数**：{status.total_items}  ")
        lines.append(f"**已完成**：{status.completed}  ")
        lines.append(f"**引用就绪**：{status.citation_ready}  ")
        lines.append(f"**模板警告**：{status.template_warnings} 个任务  ")
        lines.append("")

        # ── 视频列表 ──
        if items:
            lines.append("## 视频列表")
            lines.append("")
            lines.append("| # | 标题 | 状态 | 模板 | 来源 |")
            lines.append("|---|------|------|------|------|")
            for item in items:
                title = item.title or "-"
                status_label = _status_label(item.job_id, self.conn)
                tmpl = item.template_id or coll.template_id or "-"
                src = item.source_uri or "-"
                # 截断过长的来源
                if len(src) > 40:
                    src = src[:37] + "..."
                lines.append(
                    f"| {item.item_index + 1} | {title} | {status_label} "
                    f"| {tmpl} | {src} |"
                )
            lines.append("")

        # ── 每节摘要 ──
        if items:
            lines.append("## 每节摘要")
            lines.append("")
            for item in items:
                lines.append(f"### {item.item_index + 1}. {item.title or '未命名'}")
                lines.append("")

                # 笔记路径
                note_path = item.note_path or self._find_note_path(item.job_id)
                if note_path:
                    lines.append(f"- 笔记：{os.path.basename(note_path)}")
                else:
                    lines.append("- 笔记：*未生成*")

                # 时长
                elapsed = self._get_elapsed(item.job_id)
                if elapsed:
                    lines.append(f"- 时长：{_format_seconds(elapsed)}")

                # 关键块数
                block_count = self._get_block_count(item.job_id)
                if block_count > 0:
                    lines.append(f"- 关键块：{block_count}")

                # 引用状态
                citation_status = self._get_citation_status(item.job_id)
                lines.append(f"- 引用：{citation_status}")

                # heading 预览
                if note_path:
                    hds = _extract_headings(note_path)
                    if hds:
                        for h in hds[:5]:
                            lines.append(f"  - {h}")

                lines.append("")

        # ── 关键概念索引 ──
        concept_index = self._build_concept_index(items)
        if concept_index:
            lines.append("## 关键概念索引")
            lines.append("")
            # 按频次降序排列
            sorted_concepts = sorted(
                concept_index.items(), key=lambda x: len(x[1]), reverse=True
            )
            for concept, indices in sorted_concepts[:30]:
                index_str = "、".join(f"第 {i}" for i in sorted(indices))
                lines.append(f"- **{concept}**：{index_str}")
            lines.append("")

        # ── 质量警告 ──
        warnings = self._collect_quality_warnings(items)
        if warnings:
            lines.append("## 质量警告")
            lines.append("")
            for w in warnings:
                lines.append(f"- {w}")
            lines.append("")

        # ── Footer ──
        lines.append("---")
        lines.append(f"*由 video-notes-ai 自动生成*")

        return "\n".join(lines)

    # ── 内部查询方法 ──────────────────────────────────────

    def _find_note_path(self, job_id: str) -> str | None:
        """从 processing_runs 获取 output_path。"""
        row = self.conn.execute(
            "SELECT output_path FROM processing_runs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row and row[0]:
            return str(row[0])
        return None

    def _get_elapsed(self, job_id: str) -> float | None:
        """获取 job 总耗时（秒）。"""
        row = self.conn.execute(
            "SELECT elapsed_sec FROM processing_runs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row and row[0] is not None:
            return float(row[0])
        return None

    def _get_block_count(self, job_id: str) -> int:
        """获取 knowledge_blocks 数量。"""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM knowledge_blocks WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return row[0] if row else 0

    def _get_citation_status(self, job_id: str) -> str:
        """获取 citation 状态文本。"""
        if not self._db_path:
            return "unknown"
        try:
            from src.application.provenance.indexer import ProvenanceIndexer
            indexer = ProvenanceIndexer(self._db_path)
            pv = indexer.check_provenance_status(job_id)
            if pv.get("is_citation_ready"):
                return "ready"
            if pv.get("indexed"):
                return "indexed (no citations)"
            return "not indexed"
        except Exception:
            return "unknown"

    def _build_concept_index(
        self, items: list[CollectionItem]
    ) -> dict[str, set[int]]:
        """构建跨视频概念索引。

        从 knowledge_blocks 表中提取 block_type=concept 的条目，
        按 title 去重，映射到对应的 item_index。
        """
        if not items:
            return {}

        job_ids = [it.job_id for it in items]
        placeholders = ",".join("?" * len(job_ids))
        rows = self.conn.execute(
            f"""
            SELECT job_id, title
            FROM knowledge_blocks
            WHERE job_id IN ({placeholders})
            ORDER BY job_id, id
            """,
            job_ids,
        ).fetchall()

        # job_id → item_index 映射
        index_map: dict[str, int] = {
            it.job_id: it.item_index + 1 for it in items
        }

        # concept → set of item_index
        concepts: dict[str, set[int]] = defaultdict(set)
        for row in rows:
            jid, title = row[0], (row[1] or "").strip()
            if not title:
                continue
            idx = index_map.get(jid)
            if idx is not None:
                concepts[title].add(idx)

        return dict(concepts)

    def _collect_quality_warnings(self, items: list[CollectionItem]) -> list[str]:
        """收集所有质量警告。"""
        warnings: list[str] = []

        for item in items:
            # 检查 template_validation.json
            row = self.conn.execute(
                "SELECT job_dir FROM processing_runs WHERE job_id = ?",
                (item.job_id,),
            ).fetchone()
            if row and row[0]:
                vp = os.path.join(
                    str(row[0]), "artifacts", "template_validation.json"
                )
                try:
                    with open(vp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for w in data.get("warnings", []):
                        label = (
                            f"第 {item.item_index + 1} 讲（{item.title or item.job_id[:8]}）："
                            f"模板校验 - {w.get('message', w) if isinstance(w, dict) else w}"
                        )
                        warnings.append(label)
                except (FileNotFoundError, json.JSONDecodeError):
                    pass

            # 检查 provenance
            try:
                from src.application.provenance.indexer import ProvenanceIndexer
                indexer = ProvenanceIndexer(self._db_path)
                pv = indexer.check_provenance_status(item.job_id)
                if not pv.get("indexed"):
                    label = (
                        f"第 {item.item_index + 1} 讲（{item.title or item.job_id[:8]}）："
                        f"来源索引未完成"
                    )
                    warnings.append(label)
            except Exception:
                pass

        return warnings


# ── 辅助函数 ─────────────────────────────────────────────────

def _status_label(job_id: str, conn: sqlite3.Connection) -> str:
    """获取人类可读的状态标签。"""
    row = conn.execute(
        "SELECT status FROM processing_runs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if row:
        status = row[0]
        labels = {
            "completed": "✅",
            "failed": "❌",
            "cancelled": "⊘",
            "paused": "⏸",
            "pending": "⏳",
        }
        return labels.get(status, "🔄")
    return "⏳"


def _format_seconds(seconds: float) -> str:
    """格式化秒数为 HH:MM:SS。"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
