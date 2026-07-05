"""ProvenanceIndexer — 从 artifacts/ 读取产物并写入 provenance 表。

核心职责：
1. 读取 .jobs/{job_id}/artifacts/ 下的所有阶段产物
2. 解析 transcript.json（分段 + 时间戳）
3. 索引帧截图（frame_assets）
4. 读取知识块（knowledge_blocks）并为每个块建立 block_sources
5. 幂等：重复调用不产生重复记录

设计原则：
- 只做 I/O + DB 写入，不做语义分析（语义交给 SourceLinker）
- 失败不影响主管线（由 orchestrator try/except 包装）
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.application.provenance.models import SourceRef, ProvenanceBlock
from src.application.provenance.linker import SourceLinker
from src.infrastructure.db.gateway import DatabaseGateway
from src.infrastructure.db.repositories.provenance_repository import ProvenanceRepository


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProvenanceIndexResult:
    """ProvenanceIndexer.index_job() 的返回值。"""

    job_id: str
    segments_count: int = 0
    frames_count: int = 0
    ocr_count: int = 0
    blocks_count: int = 0
    sources_count: int = 0
    warnings: list[str] = field(default_factory=list)
    success: bool = True


class ProvenanceIndexer:
    """从 artifacts 构建 provenance 数据。

    用法：
        indexer = ProvenanceIndexer(db_path)
        result = indexer.index_job(job_id, job_dir=".jobs/abc123")
    """

    def __init__(
        self,
        db_path: str,
        linker: SourceLinker | None = None,
    ):
        """初始化索引器。

        Args:
            db_path: SQLite 数据库路径。
            linker: 可选的自定义 SourceLinker，默认创建新实例。
        """
        self._db_path = db_path
        self._linker = linker or SourceLinker()

    # ── 主入口 ──────────────────────────────────────────────

    def index_job(
        self,
        job_id: str,
        job_dir: str | None = None,
        *,
        source_type: str = "url",
        source_uri: str = "",
        title: str | None = None,
        duration: float | None = None,
        local_video_path: str | None = None,
        frames: list[dict] | None = None,
        dry_run: bool = False,
    ) -> ProvenanceIndexResult:
        """为单个 job 构建完整的 provenance 数据。

        幂等：重复调用会先清理旧数据再重建（按 job_id 匹配）。

        Args:
            job_id: 任务 ID。
            job_dir: .jobs/{job_id}/ 目录路径（默认 .jobs/{job_id}/）。
            source_type: "url" 或 "local"。
            source_uri: 原始来源 URI。
            title: 视频标题。
            duration: 视频时长（秒）。
            local_video_path: 本地视频文件路径。
            dry_run: 仅计算和验证，不写入数据库。

        Returns:
            ProvenanceIndexResult 包含各表的写入行数统计。
        """
        if job_dir is None:
            job_dir = os.path.join(".jobs", job_id)

        result = ProvenanceIndexResult(job_id=job_id)

        try:
            if not dry_run:
                # 0. 清理旧数据（幂等）
                self._clear_job(job_id)

                # 1. 写入 video_sources
                self._index_video_source(
                    job_id, source_type, source_uri, title, duration, local_video_path,
                )

            # 2. 解析 transcript.json → transcript_segments
            if dry_run:
                result.segments_count = self._count_transcript_segments(job_dir)
            else:
                result.segments_count = self._index_transcript(job_id, job_dir)

            # 3. 索引截帧 → frame_assets。优先使用 ArtifactWriter 已复制到
            # 最终输出目录的路径，避免 .jobs 清理后产生失效引用。
            if dry_run:
                result.frames_count = len(frames or []) or self._count_frames(job_dir)
            elif frames is not None:
                result.frames_count, result.ocr_count = self._index_frame_records(
                    job_id, frames, job_dir
                )
            else:
                result.frames_count = self._index_frames(job_id, job_dir)

            # 4. 读取知识块 → 建立 block_sources
            blocks = self._load_blocks_for_job(job_id)
            result.blocks_count = len(blocks)
            if blocks:
                if dry_run:
                    # dry_run: 仅计算链接数量，不写入
                    transcript_segs = self._load_transcript_segments(job_id) if not dry_run else self._parse_transcript_for_dry_run(job_dir)
                    frame_list = self._load_frame_assets(job_id) if not dry_run else []
                    dummy_count = 0
                    for block in blocks:
                        pb = self._block_to_provenance(block, job_id)
                        sources = self._linker.link_block(pb, transcript_segs, frame_list)
                        dummy_count += len(sources)
                    result.sources_count = dummy_count
                else:
                    result.sources_count = self._link_and_save_sources(
                        job_id, blocks, job_dir,
                    )

        except Exception as e:
            result.success = False
            result.warnings.append(f"索引失败: {e}")

        return result

    def _block_to_provenance(self, block: dict, job_id: str) -> ProvenanceBlock:
        """将 DB row dict 转为 ProvenanceBlock。"""
        return ProvenanceBlock(
            job_id=job_id,
            block_index=block.get("block_index", 0),
            block_type=block.get("block_type", "concept"),
            title=block.get("title"),
            content=block.get("content", ""),
            start_time=block.get("start_time") or block.get("source_timestamp"),
            end_time=block.get("end_time"),
            confidence=block.get("confidence", 1.0),
        )

    # ── dry_run 辅助方法 ────────────────────────────────────

    def _count_transcript_segments(self, job_dir: str) -> int:
        """dry_run: 仅计算转写分段数量，不写入 DB。"""
        transcript_path = os.path.join(job_dir, "artifacts", "transcript.json")
        if not os.path.isfile(transcript_path):
            return 0
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return len(data.get("segments", []))
        except (json.JSONDecodeError, OSError):
            return 0

    def _count_frames(self, job_dir: str) -> int:
        """dry_run: 仅计算截图数量，不写入 DB。"""
        frames_dir = os.path.join(job_dir, "artifacts", "frames")
        if not os.path.isdir(frames_dir):
            return 0
        image_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        frame_set: set[str] = set()
        for ext in image_exts:
            for fp in Path(frames_dir).glob(f"*{ext}"):
                frame_set.add(str(fp.resolve()))
            for fp in Path(frames_dir).glob(f"*{ext.upper()}"):
                frame_set.add(str(fp.resolve()))
        return len(frame_set)

    def _parse_transcript_for_dry_run(self, job_dir: str) -> list[dict]:
        """dry_run: 从 artifacts 直接解析转写分段（不从 DB 读）。"""
        transcript_path = os.path.join(job_dir, "artifacts", "transcript.json")
        if not os.path.isfile(transcript_path):
            return []
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            segments = data.get("segments", [])
            result: list[dict] = []
            for i, seg in enumerate(segments):
                result.append({
                    "id": i,
                    "start_time": float(seg.get("start", 0)),
                    "end_time": float(seg.get("end", 0)),
                    "text": str(seg.get("text", "")),
                })
            return result
        except (json.JSONDecodeError, OSError):
            return []

    # ── 清理 ────────────────────────────────────────────────

    def _clear_job(self, job_id: str) -> None:
        """删除指定 job 的所有 provenance 记录（幂等）。"""
        with DatabaseGateway(self._db_path).connection() as conn:
            ProvenanceRepository(conn).clear_job(job_id)

    # ── video_sources ───────────────────────────────────────

    def _index_video_source(
        self,
        job_id: str,
        source_type: str,
        source_uri: str,
        title: str | None,
        duration: float | None,
        local_video_path: str | None,
    ) -> None:
        with DatabaseGateway(self._db_path).connection() as conn:
            ProvenanceRepository(conn).index_video_source(
                job_id, source_type, source_uri, title, duration, local_video_path,
            )

    # ── transcript_segments ─────────────────────────────────

    def _index_transcript(self, job_id: str, job_dir: str) -> int:
        """解析 artifacts/transcript.json 写入 transcript_segments。

        返回写入的 segment 数量。
        """
        transcript_path = os.path.join(job_dir, "artifacts", "transcript.json")
        if not os.path.isfile(transcript_path):
            return 0

        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return 0

        segments = data.get("segments", [])
        if not segments:
            return 0

        segment_dicts = [
            {
                "segment_index": i,
                "start_time": float(s.get("start", 0)),
                "end_time": float(s.get("end", 0)),
                "text": str(s.get("text", "")),
            }
            for i, s in enumerate(segments)
        ]
        with DatabaseGateway(self._db_path).connection() as conn:
            return ProvenanceRepository(conn).index_transcript_segments(job_id, segment_dicts)

    # ── frame_assets ────────────────────────────────────────

    def _index_frame_records(
        self, job_id: str, frames: list[dict], job_dir: str
    ) -> tuple[int, int]:
        """Index final exported frame paths and their OCR text."""
        temp_root = os.path.abspath(os.path.join(job_dir, "temp"))
        frame_rows: list[dict] = []
        ocr_rows: list[dict] = []
        for item in frames:
            path = os.path.abspath(str(item.get("path") or ""))
            if not path or not os.path.isfile(path):
                continue
            # Unreferenced clean-mode frames still point into temp and will be
            # deleted after indexing; do not persist those stale paths.
            try:
                if os.path.commonpath([path, temp_root]) == temp_root:
                    continue
            except ValueError:
                pass
            frame_index = len(frame_rows)
            timestamp = float(item.get("timestamp_sec", item.get("timestamp", 0.0)) or 0.0)
            frame_rows.append({
                "frame_index": frame_index,
                "timestamp": timestamp,
                "path": path,
                "perceptual_hash": item.get("perceptual_hash"),
            })
            text = str(item.get("ocr_text") or "").strip()
            if text:
                ocr_rows.append({
                    "frame_index": frame_index,
                    "timestamp": timestamp,
                    "text": text,
                    "confidence": item.get("ocr_confidence"),
                })
        with DatabaseGateway(self._db_path).connection() as conn:
            repo = ProvenanceRepository(conn)
            frame_count = repo.index_frame_assets(job_id, frame_rows)
            ocr_count = repo.index_ocr_results(job_id, ocr_rows)
        return frame_count, ocr_count

    def _index_frames(self, job_id: str, job_dir: str) -> int:
        """索引 artifacts/frames/ 目录下的截图。

        返回写入的帧数量。
        """
        frames_dir = os.path.join(job_dir, "artifacts", "frames")
        if not os.path.isdir(frames_dir):
            return 0

        # 列出所有图片文件（Windows 大小写不敏感需去重）
        image_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        frame_set: set[str] = set()
        for ext in image_exts:
            for fp in Path(frames_dir).glob(f"*{ext}"):
                frame_set.add(str(fp.resolve()))
            for fp in Path(frames_dir).glob(f"*{ext.upper()}"):
                frame_set.add(str(fp.resolve()))

        if not frame_set:
            return 0

        frame_files = sorted(frame_set)
        frames = []
        for idx, fp in enumerate(frame_files):
            # 尝试从文件名推断时间戳（格式：frame_<timestamp>.ext）
            path_obj = Path(fp)
            timestamp: float = 0.0
            name = path_obj.stem
            parts = name.split("_")
            if len(parts) >= 2:
                try:
                    timestamp = float(parts[-1])
                except ValueError:
                    pass
            frames.append({
                "frame_index": idx,
                "timestamp": timestamp,
                "path": fp,
            })

        with DatabaseGateway(self._db_path).connection() as conn:
            return ProvenanceRepository(conn).index_frame_assets(job_id, frames)

    # ── knowledge_blocks + block_sources ────────────────────

    def _load_blocks_for_job(self, job_id: str) -> list[dict]:
        """从 knowledge_blocks 表读取指定 job 的所有块。

        同时尝试更新 note_id_int（从 notes 表映射）。
        """
        with DatabaseGateway(self._db_path).connection() as conn:
            conn.execute(
                """UPDATE knowledge_blocks
                   SET note_id_int = (
                       SELECT n.id FROM notes n
                       WHERE n.rel_path = knowledge_blocks.note_id
                   )
                   WHERE note_id_int IS NULL
                     AND job_id = ?
                     AND note_id IS NOT NULL
                     AND note_id != ''""",
                (job_id,),
            )
            return ProvenanceRepository(conn).load_blocks_for_job(job_id)

    def _link_and_save_sources(
        self, job_id: str, blocks: list[dict], job_dir: str,
    ) -> int:
        """为每个知识块建立来源链接，写入 block_sources 表。

        返回写入的 source 总数。
        """
        # 读取转写分段和帧数据（供 linker 使用）
        transcript_segments = self._load_transcript_segments(job_id)
        frame_assets = self._load_frame_assets(job_id)

        all_block_sources: list[dict] = []
        for block in blocks:
            block_id = block["id"]

            # 构建 ProvenanceBlock 供 linker 使用
            pb = self._block_to_provenance(block, job_id)

            # 链接来源
            sources = self._linker.link_block(
                pb, transcript_segments, frame_assets,
            )

            # 收集 block_sources 数据
            for src in sources:
                all_block_sources.append({
                    "block_id": block_id,
                    "source_kind": src.source_kind,
                    "source_id": src.source_id or 0,
                    "relevance": src.relevance,
                    "quote": src.quote,
                })

        with DatabaseGateway(self._db_path).connection() as conn:
            repo = ProvenanceRepository(conn)
            repo.link_block_sources(all_block_sources)

        return len(all_block_sources)

    def _load_transcript_segments(self, job_id: str) -> list[dict]:
        """从 DB 读取转写分段。"""
        with DatabaseGateway(self._db_path).connection() as conn:
            return ProvenanceRepository(conn).load_transcript_segments(job_id)

    def _load_frame_assets(self, job_id: str) -> list[dict]:
        """从 DB 读取帧资产。"""
        with DatabaseGateway(self._db_path).connection() as conn:
            return ProvenanceRepository(conn).load_frame_assets(job_id)

    # ── 状态查询 ────────────────────────────────────────────

    def check_provenance_status(self, job_id: str) -> dict:
        """检查指定 job 的 provenance 索引状态。

        返回 dict:
            indexed: bool           — 是否有 provenance 数据
            segments: int           — transcript_segments 行数
            frames: int             — frame_assets 行数
            ocr: int                — ocr_results 行数
            blocks_with_sources: int — 有来源链接的知识块数
            total_blocks: int       — 知识块总数
            total_sources: int      — block_sources 总行数
            is_citation_ready: bool — 能否生成 citations
        """
        with DatabaseGateway(self._db_path).connection() as conn:
            repo = ProvenanceRepository(conn)
            counts = repo.check_provenance_status(job_id)

            total_blocks = conn.execute(
                "SELECT COUNT(*) as n FROM knowledge_blocks WHERE job_id = ?",
                (job_id,),
            ).fetchone()["n"]

            blocks_with_sources = conn.execute(
                """SELECT COUNT(DISTINCT kb.id) as n
                   FROM knowledge_blocks kb
                   INNER JOIN block_sources bs ON bs.block_id = kb.id
                   WHERE kb.job_id = ?""",
                (job_id,),
            ).fetchone()["n"]

            indexed = counts["transcript_segments"] > 0 or counts["frame_assets"] > 0
            citation_ready = indexed and blocks_with_sources > 0

            return {
                "indexed": indexed,
                "segments": counts["transcript_segments"],
                "frames": counts["frame_assets"],
                "ocr": counts["ocr_results"],
                "blocks_with_sources": blocks_with_sources,
                "total_blocks": total_blocks,
                "total_sources": counts["block_sources"],
                "is_citation_ready": citation_ready,
            }

    def check_all_jobs_provenance(self) -> list[dict]:
        """检查所有已完成的 job 的 provenance 状态。

        返回 list[dict]，每项含 job_id + provenance status 字段。
        """
        job_ids: list[str] = []
        with DatabaseGateway(self._db_path).connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT job_id FROM knowledge_blocks WHERE job_id IS NOT NULL AND job_id != ''
                   UNION
                   SELECT DISTINCT job_id FROM transcript_segments
                   ORDER BY job_id""",
            ).fetchall()
            job_ids = [r["job_id"] for r in rows]

        results: list[dict] = []
        for jid in job_ids:
            pv = self.check_provenance_status(jid)
            pv["job_id"] = jid
            results.append(pv)
        return results
