"""JobRepository — CRUD for the processing_runs table."""
from __future__ import annotations

import os
import uuid
import json
from datetime import datetime
from typing import Optional

import sqlite3

from src.domain.job_state import JobState, get_stage_artifact, get_stage_order


class JobRepository:
    """Encapsulates all SQL access for the processing_runs table.

    Does NOT manage transactions — the caller (e.g. DatabaseGateway
    connection context manager) is responsible for commit/rollback.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ── lifecycle ────────────────────────────────────────────────

    def start_run(
        self,
        input_path: str,
        title: Optional[str] = None,
        job_dir: Optional[str] = None,
        job_id: Optional[str] = None,
        request_snapshot: dict | None = None,
        parent_run_id: int | None = None,
        attempt: int = 1,
    ) -> int:
        """Create a new processing run record, return run_id."""
        job_id = job_id or str(uuid.uuid4())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = self._conn.execute(
            """INSERT INTO processing_runs
                  (input_path, title, status, stage, stage_started_at,
                   job_id, job_dir, started_at, request_json, parent_run_id,
                   attempt, heartbeat_at, last_active_stage)
               VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                input_path,
                title,
                JobState.PENDING.value,
                now,
                job_id,
                job_dir,
                now,
                json.dumps(request_snapshot or {}, ensure_ascii=False),
                parent_run_id,
                max(1, int(attempt)),
                now,
                JobState.PENDING.value,
            ),
        )
        return cursor.lastrowid

    def update_stage(self, run_id: int, stage: JobState) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if stage.is_running or stage == JobState.PENDING:
            self._conn.execute(
                """UPDATE processing_runs
                   SET stage = ?, last_active_stage = ?, stage_started_at = ?,
                       heartbeat_at = ?
                   WHERE id = ?""",
                (stage.value, stage.value, now, now, run_id),
            )
        else:
            self._conn.execute(
                """UPDATE processing_runs
                   SET stage = ?, stage_started_at = ?, heartbeat_at = ?
                   WHERE id = ?""",
                (stage.value, now, now, run_id),
            )

    def update_progress(
        self,
        run_id: int,
        stage: JobState,
        progress: float,
        message: str = "",
    ) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        value = max(0.0, min(100.0, float(progress)))
        self._conn.execute(
            """UPDATE processing_runs
               SET stage_started_at = CASE
                       WHEN stage <> ? OR stage_started_at IS NULL THEN ?
                       ELSE stage_started_at
                   END,
                   stage = ?, last_active_stage = ?, progress = ?,
                   progress_message = ?, heartbeat_at = ?
               WHERE id = ?""",
            (
                stage.value,
                now,
                stage.value,
                stage.value,
                value,
                message or None,
                now,
                run_id,
            ),
        )

    def complete_run(
        self,
        run_id: int,
        output_path: Optional[str] = None,
        transcript_path: Optional[str] = None,
        elapsed_sec: float = 0.0,
        frames_count: int = 0,
        blocks_count: int = 0,
        note_id: Optional[int] = None,
    ) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            """UPDATE processing_runs
               SET status = 'completed', stage = ?,
                   output_path = ?, transcript_path = ?,
                   elapsed_sec = ?, frames_count = ?,
                   blocks_count = ?, note_id = ?, completed_at = ?,
                   progress = 100, progress_message = '任务完成', heartbeat_at = ?
               WHERE id = ?""",
            (JobState.COMPLETED.value, output_path, transcript_path,
             elapsed_sec, frames_count, blocks_count, note_id, now, now, run_id),
        )

    def fail_run(self, run_id: int, error_message: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            """UPDATE processing_runs
               SET status = 'failed', stage = ?,
                   error_message = ?, completed_at = ?, heartbeat_at = ?
               WHERE id = ?""",
            (JobState.FAILED.value, error_message, now, now, run_id),
        )

    def mark_interrupted_runs(self, reason: str) -> int:
        """Mark stale workers from a previous engine process as resumable."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = self._conn.execute(
            """UPDATE processing_runs
               SET status = 'interrupted', stage = ?, error_message = ?,
                   interrupted_at = ?, completed_at = ?, heartbeat_at = ?
               WHERE status IN ('running', 'pausing', 'cancelling')""",
            (JobState.INTERRUPTED.value, reason, now, now, now),
        )
        return max(cursor.rowcount, 0)

    def request_stop(self, run_id: int, action: str) -> None:
        if action not in {"pause", "cancel"}:
            raise ValueError(f"unsupported stop action: {action}")
        stage = JobState.PAUSING if action == "pause" else JobState.CANCELLING
        status = "pausing" if action == "pause" else "cancelling"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            """UPDATE processing_runs
               SET status = ?, stage = ?, stage_started_at = ?, error_message = NULL
               WHERE id = ?""",
            (status, stage.value, now, run_id),
        )

    def pause_run(self, run_id: int) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            """UPDATE processing_runs
               SET status = 'paused', stage = ?,
                   completed_at = ?, error_message = NULL
               WHERE id = ?""",
            (JobState.PAUSED.value, now, run_id),
        )

    def cancel_run(self, run_id: int) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            """UPDATE processing_runs
               SET status = 'cancelled', stage = ?,
                   completed_at = ?
               WHERE id = ?""",
            (JobState.CANCELLED.value, now, run_id),
        )

    def prepare_resume(self, run_id: int) -> bool:
        """Reset terminal/error fields before resuming an existing run."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = self._conn.execute(
            """UPDATE processing_runs
               SET status = 'running',
                   error_message = NULL,
                   completed_at = NULL,
                   interrupted_at = NULL,
                   stage = COALESCE(NULLIF(last_active_stage, ''), 'pending'),
                   stage_started_at = ?, heartbeat_at = ?
               WHERE id = ?""",
            (now, now, run_id),
        )
        return cursor.rowcount > 0

    def detach_workspace(self, run_id: int) -> bool:
        """Mark a run as no longer resumable after its workspace is removed."""
        cursor = self._conn.execute(
            "UPDATE processing_runs SET job_dir = NULL WHERE id = ?",
            (run_id,),
        )
        return cursor.rowcount > 0

    # ── queries ──────────────────────────────────────────────────

    def get_job(self, run_id: int) -> dict | None:
        return self._fetch_one(
            "SELECT * FROM processing_runs WHERE id = ?", (run_id,)
        )

    def get_by_job_id(self, job_id: str) -> dict | None:
        return self._fetch_one(
            "SELECT * FROM processing_runs WHERE job_id = ?", (job_id,)
        )

    def list_jobs(
        self,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        order_by: str = "started_at DESC",
        *,
        include_hidden: bool = False,
    ) -> list[dict]:
        # Whitelist order_by to prevent SQL injection
        _ALLOWED_ORDER = {
            "started_at DESC", "started_at ASC",
            "completed_at DESC", "completed_at ASC",
            "id DESC", "id ASC",
            "status DESC", "status ASC",
            "input DESC", "input ASC",
        }
        if order_by not in _ALLOWED_ORDER:
            order_by = "started_at DESC"

        clauses: list[str] = []
        params: list[object] = []
        if not include_hidden:
            clauses.append("COALESCE(is_hidden, 0) = 0")
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM processing_runs{where} ORDER BY {order_by} LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all_jobs(self, limit: int = 100000) -> list[dict]:
        return self.list_jobs(limit=limit, include_hidden=True)

    def count_jobs(self, status: str | None = None, *, include_hidden: bool = False) -> int:
        clauses: list[str] = []
        params: list[object] = []
        if not include_hidden:
            clauses.append("COALESCE(is_hidden, 0) = 0")
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        row = self._conn.execute(
            f"SELECT COUNT(*) FROM processing_runs{where}", tuple(params)
        ).fetchone()
        return row[0] if row else 0

    # ── resume logic ─────────────────────────────────────────────

    def get_resumable_stage(self, run_id: int) -> Optional[JobState]:
        """Find next uncompleted stage for a failed/interrupted job.

        Returns None if job not found, completed, or destructively cancelled.
        """
        job = self.get_job(run_id)
        if job is None:
            return None
        if job["status"] in ("completed", "cancelled"):
            return None

        order = get_stage_order()
        last_completed_idx = -1
        for i, stage in enumerate(order):
            if job.get("job_dir") and os.path.exists(job["job_dir"]):
                artifact = get_stage_artifact(stage)
                if artifact and os.path.exists(os.path.join(job["job_dir"], artifact)):
                    last_completed_idx = i
        if last_completed_idx + 1 < len(order):
            return order[last_completed_idx + 1]
        return None

    # ── delete ────────────────────────────────────────────────────

    def delete_run(self, run_id: int) -> bool:
        """Delete a single processing run by id. Returns True if deleted."""
        cursor = self._conn.execute(
            "DELETE FROM processing_runs WHERE id = ?", (run_id,)
        )
        return cursor.rowcount > 0

    def delete_run_with_index(self, run_id: int, job_id: str) -> bool:
        """Atomically delete one run, its provenance, and orphan note copies."""
        self.clear_job_related_index(job_id)
        deleted = self.delete_run(run_id)
        if deleted:
            self.delete_orphan_notes()
        return deleted

    def delete_orphan_notes(self) -> int:
        """Delete note-index copies no longer referenced by jobs or collections.

        The exported Markdown files are not stored in SQLite and remain on
        disk.  Collection note paths are protected even when a legacy row has
        lost its processing-run link.
        """
        cursor = self._conn.execute(
            """
            DELETE FROM notes
            WHERE NOT EXISTS (
                SELECT 1 FROM processing_runs pr WHERE pr.note_id = notes.id
            )
              AND NOT EXISTS (
                SELECT 1 FROM knowledge_blocks kb WHERE kb.note_id_int = notes.id
            )
              AND NOT EXISTS (
                SELECT 1
                FROM collection_items ci
                WHERE ci.note_path IS NOT NULL
                  AND TRIM(ci.note_path) != ''
                  AND (
                      REPLACE(ci.note_path, '\\', '/') = REPLACE(notes.rel_path, '\\', '/')
                      OR REPLACE(ci.note_path, '\\', '/') LIKE
                         '%/' || REPLACE(notes.rel_path, '\\', '/')
                  )
            )
            """
        )
        return max(cursor.rowcount, 0)

    def clear_all(self) -> int:
        """Hide terminal task-history rows without breaking workspace/index links."""
        cursor = self._conn.execute(
            "UPDATE processing_runs SET is_hidden = 1 WHERE status NOT IN ('running','pausing','cancelling') AND COALESCE(is_hidden, 0) = 0"
        )
        return cursor.rowcount

    def list_hidden_purge_candidates(self) -> list[dict]:
        """Return hidden terminal runs that are not owned by a collection.

        Collection items rely on the job's provenance data when rendering
        aggregate notes, so those jobs are deliberately excluded from the
        destructive cleanup path.
        """
        rows = self._conn.execute(
            """
            SELECT pr.id, pr.job_id, pr.job_dir, pr.note_id
            FROM processing_runs pr
            WHERE COALESCE(pr.is_hidden, 0) = 1
              AND pr.status NOT IN ('running','pausing','cancelling')
              AND NOT EXISTS (
                  SELECT 1 FROM collection_items ci WHERE ci.job_id = pr.job_id
              )
            ORDER BY pr.id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def count_hidden_collection_jobs(self) -> int:
        row = self._conn.execute(
            """
            SELECT COUNT(*)
            FROM processing_runs pr
            WHERE COALESCE(pr.is_hidden, 0) = 1
              AND pr.status NOT IN ('running','pausing','cancelling')
              AND EXISTS (
                  SELECT 1 FROM collection_items ci WHERE ci.job_id = pr.job_id
              )
            """
        ).fetchone()
        return int(row[0]) if row else 0

    def purge_hidden_runs(self, run_ids: list[int]) -> dict[str, int]:
        """Permanently delete selected hidden runs and their database index.

        Final exported files are external to SQLite and are not touched here.
        The caller is responsible for removing ``.jobs`` workspaces first.
        """
        unique_ids = sorted({int(run_id) for run_id in run_ids})
        stats = {
            "runs": 0,
            "video_sources": 0,
            "transcript_segments": 0,
            "frame_assets": 0,
            "ocr_results": 0,
            "knowledge_blocks": 0,
            "block_sources": 0,
            "notes": 0,
        }
        if not unique_ids:
            stats["notes"] = self.delete_orphan_notes()
            return stats

        self._conn.execute("DROP TABLE IF EXISTS temp.purge_run_ids")
        self._conn.execute(
            "CREATE TEMP TABLE purge_run_ids(run_id INTEGER PRIMARY KEY)"
        )
        self._conn.executemany(
            "INSERT OR IGNORE INTO purge_run_ids(run_id) VALUES (?)",
            [(run_id,) for run_id in unique_ids],
        )

        self._conn.execute("DROP TABLE IF EXISTS temp.purge_jobs")
        self._conn.execute(
            """
            CREATE TEMP TABLE purge_jobs AS
            SELECT pr.id, pr.job_id, pr.note_id
            FROM processing_runs pr
            JOIN purge_run_ids ids ON ids.run_id = pr.id
            WHERE COALESCE(pr.is_hidden, 0) = 1
              AND pr.status NOT IN ('running','pausing','cancelling')
              AND NOT EXISTS (
                  SELECT 1 FROM collection_items ci WHERE ci.job_id = pr.job_id
              )
            """
        )

        def count(sql: str) -> int:
            row = self._conn.execute(sql).fetchone()
            return int(row[0]) if row else 0

        stats["runs"] = count("SELECT COUNT(*) FROM purge_jobs")
        stats["video_sources"] = count(
            "SELECT COUNT(*) FROM video_sources WHERE job_id IN (SELECT job_id FROM purge_jobs)"
        )
        stats["transcript_segments"] = count(
            "SELECT COUNT(*) FROM transcript_segments WHERE job_id IN (SELECT job_id FROM purge_jobs)"
        )
        stats["frame_assets"] = count(
            "SELECT COUNT(*) FROM frame_assets WHERE job_id IN (SELECT job_id FROM purge_jobs)"
        )
        stats["ocr_results"] = count(
            "SELECT COUNT(*) FROM ocr_results WHERE job_id IN (SELECT job_id FROM purge_jobs)"
        )
        stats["knowledge_blocks"] = count(
            "SELECT COUNT(*) FROM knowledge_blocks WHERE job_id IN (SELECT job_id FROM purge_jobs)"
        )
        stats["block_sources"] = count(
            """
            SELECT COUNT(*) FROM block_sources
            WHERE block_id IN (
                SELECT id FROM knowledge_blocks
                WHERE job_id IN (SELECT job_id FROM purge_jobs)
            )
            """
        )

        self._conn.execute(
            """
            DELETE FROM block_sources
            WHERE block_id IN (
                SELECT id FROM knowledge_blocks
                WHERE job_id IN (SELECT job_id FROM purge_jobs)
            )
            """
        )
        for table in (
            "ocr_results",
            "frame_assets",
            "transcript_segments",
            "video_sources",
        ):
            self._conn.execute(
                f"DELETE FROM {table} WHERE job_id IN (SELECT job_id FROM purge_jobs)"
            )
        self._conn.execute(
            "DELETE FROM knowledge_blocks WHERE job_id IN (SELECT job_id FROM purge_jobs)"
        )
        self._conn.execute(
            "DELETE FROM processing_runs WHERE id IN (SELECT id FROM purge_jobs)"
        )

        # Notes contain duplicate full Markdown used only for search/indexing.
        # Also sweep legacy orphan notes left by older per-task deletions.
        stats["notes"] = self.delete_orphan_notes()

        self._conn.execute("DROP TABLE IF EXISTS temp.purge_jobs")
        self._conn.execute("DROP TABLE IF EXISTS temp.purge_run_ids")
        return stats

    def clear_job_related_index(self, job_id: str) -> None:
        """Delete provenance rows owned by a job, without touching final files."""
        self._conn.execute(
            "DELETE FROM block_sources WHERE block_id IN "
            "(SELECT id FROM knowledge_blocks WHERE job_id = ?)",
            (job_id,),
        )
        for table in ("ocr_results", "frame_assets", "transcript_segments", "video_sources"):
            self._conn.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
        self._conn.execute("DELETE FROM knowledge_blocks WHERE job_id = ?", (job_id,))

    # ── helpers ──────────────────────────────────────────────────

    def _fetch_one(self, query: str, params: tuple = ()) -> dict | None:
        row = self._conn.execute(query, params).fetchone()
        return dict(row) if row else None