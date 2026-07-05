"""ProvenanceRepository — SQL for provenance tables + knowledge_blocks queries."""
from __future__ import annotations

import sqlite3


class ProvenanceRepository:
    """Encapsulates all SQL access for provenance tables.

    Tables: video_sources, transcript_segments, frame_assets,
            ocr_results, block_sources.

    Does NOT manage transactions — the caller is responsible for commit.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ── lifecycle ──────────────────────────────────────────────

    def clear_job(self, job_id: str) -> None:
        """Delete all provenance records for a job (idempotent)."""
        self._conn.execute(
            "DELETE FROM block_sources WHERE block_id IN "
            "(SELECT id FROM knowledge_blocks WHERE job_id=?)",
            (job_id,),
        )
        self._conn.execute(
            "DELETE FROM ocr_results WHERE job_id=?", (job_id,)
        )
        self._conn.execute(
            "DELETE FROM frame_assets WHERE job_id=?", (job_id,)
        )
        self._conn.execute(
            "DELETE FROM transcript_segments WHERE job_id=?", (job_id,)
        )
        self._conn.execute(
            "DELETE FROM video_sources WHERE job_id=?", (job_id,)
        )

    # ── indexers ───────────────────────────────────────────────

    def index_video_source(
        self,
        job_id: str,
        source_type: str,
        source_uri: str,
        title: str | None = None,
        duration: float | None = None,
        local_video_path: str | None = None,
    ) -> None:
        """INSERT OR REPLACE INTO video_sources."""
        self._conn.execute(
            """INSERT OR REPLACE INTO video_sources
               (job_id, source_type, source_uri, title, duration, local_video_path)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (job_id, source_type, source_uri, title, duration, local_video_path),
        )

    def index_transcript_segments(
        self, job_id: str, segments: list[dict]
    ) -> int:
        """INSERT OR REPLACE INTO transcript_segments for each segment.

        Each segment dict keys: segment_index, start_time, end_time, text.
        Returns count of inserted segments.
        """
        count = 0
        for seg in segments:
            self._conn.execute(
                """INSERT OR REPLACE INTO transcript_segments
                   (job_id, segment_index, start_time, end_time, text)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    job_id,
                    seg["segment_index"],
                    seg["start_time"],
                    seg["end_time"],
                    seg["text"],
                ),
            )
            count += 1
        return count

    def index_frame_assets(
        self, job_id: str, frames: list[dict]
    ) -> int:
        """INSERT OR REPLACE INTO frame_assets for each frame.

        Each frame dict keys: frame_index, timestamp, path,
        perceptual_hash (optional).
        Returns count of inserted frames.
        """
        count = 0
        for frame in frames:
            self._conn.execute(
                """INSERT OR REPLACE INTO frame_assets
                   (job_id, frame_index, timestamp, path, perceptual_hash)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    job_id,
                    frame["frame_index"],
                    frame["timestamp"],
                    frame["path"],
                    frame.get("perceptual_hash"),
                ),
            )
            count += 1
        return count

    def index_ocr_results(self, job_id: str, rows: list[dict]) -> int:
        """Insert OCR text linked to indexed frame assets."""
        count = 0
        for item in rows:
            frame = self._conn.execute(
                "SELECT id FROM frame_assets WHERE job_id = ? AND frame_index = ?",
                (job_id, item["frame_index"]),
            ).fetchone()
            frame_id = frame[0] if frame else None
            self._conn.execute(
                """INSERT INTO ocr_results
                   (job_id, frame_id, timestamp, text, confidence)
                   VALUES (?, ?, ?, ?, ?)""",
                (job_id, frame_id, item.get("timestamp"), item["text"], item.get("confidence")),
            )
            count += 1
        return count

    def link_block_sources(self, block_sources: list[dict]) -> None:
        """INSERT OR IGNORE INTO block_sources for each.

        Each dict keys: block_id, source_kind, source_id,
        relevance (default 1.0), quote (optional).
        """
        for bs in block_sources:
            self._conn.execute(
                """INSERT OR IGNORE INTO block_sources
                   (block_id, source_kind, source_id, relevance, quote)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    bs["block_id"],
                    bs["source_kind"],
                    bs["source_id"],
                    bs.get("relevance", 1.0),
                    bs.get("quote"),
                ),
            )

    # ── loaders ────────────────────────────────────────────────

    def load_blocks_for_job(self, job_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM knowledge_blocks WHERE job_id=? ORDER BY id",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def load_transcript_segments(self, job_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM transcript_segments WHERE job_id=? ORDER BY segment_index",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def load_frame_assets(self, job_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM frame_assets WHERE job_id=? ORDER BY frame_index",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── status ─────────────────────────────────────────────────

    def check_provenance_status(self, job_id: str) -> dict:
        """Return counts of records per table for a job."""
        row = self._conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM video_sources WHERE job_id=?) as video_sources,
                 (SELECT COUNT(*) FROM transcript_segments WHERE job_id=?) as transcript_segments,
                 (SELECT COUNT(*) FROM frame_assets WHERE job_id=?) as frame_assets,
                 (SELECT COUNT(*) FROM block_sources
                  WHERE block_id IN (SELECT id FROM knowledge_blocks WHERE job_id=?)) as block_sources,
                 (SELECT COUNT(*) FROM ocr_results WHERE job_id=?) as ocr_results""",
            (job_id, job_id, job_id, job_id, job_id),
        ).fetchone()
        return dict(row)

    def check_all_jobs_provenance(self) -> list[dict]:
        """Return summary of provenance for all jobs with video_sources entries."""
        rows = self._conn.execute(
            """SELECT vs.job_id, vs.source_type, vs.title,
                      COUNT(DISTINCT ts.id) as segments_count,
                      COUNT(DISTINCT fa.id) as frames_count,
                      COUNT(DISTINCT bs.id) as sources_count
               FROM video_sources vs
               LEFT JOIN transcript_segments ts ON ts.job_id = vs.job_id
               LEFT JOIN frame_assets fa ON fa.job_id = vs.job_id
               LEFT JOIN block_sources bs ON bs.block_id IN
                   (SELECT id FROM knowledge_blocks WHERE job_id = vs.job_id)
               GROUP BY vs.job_id"""
        ).fetchall()
        return [dict(r) for r in rows]