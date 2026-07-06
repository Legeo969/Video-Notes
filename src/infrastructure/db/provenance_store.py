"""SQLite implementation of the provenance persistence port."""

from __future__ import annotations

from src.application.ports.provenance import ProvenanceStore
from src.infrastructure.db.gateway import DatabaseGateway
from src.infrastructure.db.repositories.provenance_repository import ProvenanceRepository


class SqliteProvenanceStore(ProvenanceStore):
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def clear_job(self, job_id: str) -> None:
        with DatabaseGateway(self._db_path).connection() as conn:
            ProvenanceRepository(conn).clear_job(job_id)

    def index_video_source(
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
                job_id, source_type, source_uri, title, duration, local_video_path
            )

    def index_transcript_segments(self, job_id: str, segments: list[dict]) -> int:
        with DatabaseGateway(self._db_path).connection() as conn:
            return ProvenanceRepository(conn).index_transcript_segments(job_id, segments)

    def index_frame_assets(self, job_id: str, frames: list[dict]) -> int:
        with DatabaseGateway(self._db_path).connection() as conn:
            return ProvenanceRepository(conn).index_frame_assets(job_id, frames)

    def index_ocr_results(self, job_id: str, rows: list[dict]) -> int:
        with DatabaseGateway(self._db_path).connection() as conn:
            return ProvenanceRepository(conn).index_ocr_results(job_id, rows)

    def link_block_sources(self, block_sources: list[dict]) -> None:
        with DatabaseGateway(self._db_path).connection() as conn:
            ProvenanceRepository(conn).link_block_sources(block_sources)

    def load_blocks_for_job(self, job_id: str) -> list[dict]:
        with DatabaseGateway(self._db_path).connection() as conn:
            return ProvenanceRepository(conn).load_blocks_for_job(job_id)

    def load_transcript_segments(self, job_id: str) -> list[dict]:
        with DatabaseGateway(self._db_path).connection() as conn:
            return ProvenanceRepository(conn).load_transcript_segments(job_id)

    def load_frame_assets(self, job_id: str) -> list[dict]:
        with DatabaseGateway(self._db_path).connection() as conn:
            return ProvenanceRepository(conn).load_frame_assets(job_id)

    def update_block_note_links(self, job_id: str) -> None:
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

    def count_blocks(self, job_id: str) -> int:
        with DatabaseGateway(self._db_path).connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) as n FROM knowledge_blocks WHERE job_id = ?",
                (job_id,),
            ).fetchone()["n"]

    def count_blocks_with_sources(self, job_id: str) -> int:
        with DatabaseGateway(self._db_path).connection() as conn:
            return conn.execute(
                """SELECT COUNT(DISTINCT kb.id) as n
                   FROM knowledge_blocks kb
                   INNER JOIN block_sources bs ON bs.block_id = kb.id
                   WHERE kb.job_id = ?""",
                (job_id,),
            ).fetchone()["n"]

    def check_provenance_status(self, job_id: str) -> dict:
        with DatabaseGateway(self._db_path).connection() as conn:
            return ProvenanceRepository(conn).check_provenance_status(job_id)

    def list_indexed_job_ids(self) -> list[str]:
        with DatabaseGateway(self._db_path).connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT job_id FROM knowledge_blocks WHERE job_id IS NOT NULL AND job_id != ''
                   UNION
                   SELECT DISTINCT job_id FROM transcript_segments
                   ORDER BY job_id"""
            ).fetchall()
            return [row["job_id"] for row in rows]

