"""Processing metadata — 任务生命周期管理（SQLite 持久化）。

通过 DatabaseGateway + JobRepository 委托实现。
"""

from __future__ import annotations

from typing import Optional

from src.domain.job_state import JobState, JobRecord
from src.infrastructure.db.gateway import DatabaseGateway
from src.infrastructure.db.repositories.job_repository import JobRepository


class ProcessingMetadata:
    """任务元数据持久化层。

    委托 JobRepository 完成全部 CRUD 操作。
    """

    def __init__(self, db_path: str):
        self._gateway = DatabaseGateway(db_path)
        self._gateway.initialize()

    # ── 生命周期 ──

    def start_run(
        self,
        input_path: str,
        title: Optional[str] = None,
        job_dir: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> int:
        with self._gateway.connection() as conn:
            return JobRepository(conn).start_run(input_path, title, job_dir, job_id)

    def update_stage(self, run_id: int, stage: JobState) -> None:
        with self._gateway.connection() as conn:
            JobRepository(conn).update_stage(run_id, stage)

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
        with self._gateway.connection() as conn:
            JobRepository(conn).complete_run(
                run_id, output_path, transcript_path,
                elapsed_sec, frames_count, blocks_count, note_id,
            )

    def fail_run(self, run_id: int, error_message: str) -> None:
        with self._gateway.connection() as conn:
            JobRepository(conn).fail_run(run_id, error_message)

    def request_stop(self, run_id: int, action: str) -> None:
        with self._gateway.connection() as conn:
            JobRepository(conn).request_stop(run_id, action)

    def pause_run(self, run_id: int) -> None:
        with self._gateway.connection() as conn:
            JobRepository(conn).pause_run(run_id)

    def cancel_run(self, run_id: int) -> None:
        with self._gateway.connection() as conn:
            JobRepository(conn).cancel_run(run_id)

    def prepare_resume(self, run_id: int) -> bool:
        with self._gateway.connection() as conn:
            return JobRepository(conn).prepare_resume(run_id)

    def detach_workspace(self, run_id: int) -> bool:
        with self._gateway.connection() as conn:
            return JobRepository(conn).detach_workspace(run_id)

    # ── 查询 ──

    def get_job(self, run_id: int) -> JobRecord | None:
        with self._gateway.connection() as conn:
            data = JobRepository(conn).get_job(run_id)
            if data is None:
                return None
            return self._dict_to_record(data)

    def get_job_by_job_id(self, job_id: str) -> JobRecord | None:
        with self._gateway.connection() as conn:
            data = JobRepository(conn).get_by_job_id(job_id)
            if data is None:
                return None
            return self._dict_to_record(data)

    def list_jobs(
        self,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        order_by: str = "started_at DESC",
    ) -> list[JobRecord]:
        with self._gateway.connection() as conn:
            rows = JobRepository(conn).list_jobs(
                limit=limit, offset=offset, status=status, order_by=order_by,
            )
            return [self._dict_to_record(r) for r in rows]

    def list_all_jobs(self, limit: int = 100000) -> list[JobRecord]:
        with self._gateway.connection() as conn:
            rows = JobRepository(conn).list_all_jobs(limit=limit)
            return [self._dict_to_record(r) for r in rows]

    def get_recent_runs(self, limit: int = 10) -> list[dict]:
        """获取最近的任务记录（list_jobs 的便捷别名，返回 dict 格式）。

        向后兼容：旧测试代码使用此方法名且期望下标访问。
        """
        import dataclasses
        records = self.list_jobs(limit=limit)
        return [dataclasses.asdict(r) for r in records]

    def count_jobs(self, status: str | None = None) -> int:
        with self._gateway.connection() as conn:
            return JobRepository(conn).count_jobs(status=status)

    def get_resumable_stage(self, run_id: int) -> JobState | None:
        with self._gateway.connection() as conn:
            return JobRepository(conn).get_resumable_stage(run_id)

    # ── 删除 ──

    def delete_run(self, run_id: int) -> bool:
        with self._gateway.connection() as conn:
            return JobRepository(conn).delete_run(run_id)

    def delete_run_with_index(self, run_id: int, job_id: str) -> bool:
        with self._gateway.connection() as conn:
            return JobRepository(conn).delete_run_with_index(run_id, job_id)

    def clear_all(self) -> int:
        with self._gateway.connection() as conn:
            return JobRepository(conn).clear_all()

    def list_hidden_purge_candidates(self) -> list[dict]:
        with self._gateway.connection() as conn:
            return JobRepository(conn).list_hidden_purge_candidates()

    def count_hidden_collection_jobs(self) -> int:
        with self._gateway.connection() as conn:
            return JobRepository(conn).count_hidden_collection_jobs()

    def purge_hidden_runs(self, run_ids: list[int]) -> dict[str, int]:
        with self._gateway.connection() as conn:
            return JobRepository(conn).purge_hidden_runs(run_ids)

    def clear_job_related_index(self, job_id: str) -> None:
        with self._gateway.connection() as conn:
            JobRepository(conn).clear_job_related_index(job_id)

    # ── 内部 ──

    @staticmethod
    def _dict_to_record(data: dict) -> JobRecord:
        return JobRecord(
            id=data["id"],
            job_id=data.get("job_id", ""),
            input=data["input_path"],
            title=data.get("title"),
            status=data["status"],
            stage=data.get("stage", "pending"),
            output_path=data.get("output_path"),
            transcript_path=data.get("transcript_path"),
            error_message=data.get("error_message"),
            job_dir=data.get("job_dir"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            elapsed_sec=data.get("elapsed_sec", 0.0),
            frames_count=data.get("frames_count", 0),
            blocks_count=data.get("blocks_count", 0),
            note_id=data.get("note_id"),
        )


# ── 向后兼容快捷函数 ──

def get_recent_runs(db_path: str, limit: int = 10) -> list[dict]:
    """向后兼容：从 ProcessingMetadata 获取最近的任务记录。"""
    meta = ProcessingMetadata(db_path)
    jobs = meta.list_jobs(limit=limit)
    return [
        {
            "id": j.id,
            "input_path": j.input,
            "title": j.title,
            "status": j.status,
            "output_path": j.output_path,
            "transcript_path": j.transcript_path,
            "error_message": j.error_message,
            "started_at": j.started_at,
            "completed_at": j.completed_at,
        }
        for j in jobs
    ]