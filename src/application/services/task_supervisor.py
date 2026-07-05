"""Single-owner runtime for background pipeline workers.

API handlers should never create ad-hoc threads. The supervisor is the only
component allowed to start, resume or retry a pipeline worker, which prevents
duplicate workers for the same job and centralizes lifecycle cleanup.
"""

from __future__ import annotations

import os
import threading
from typing import Callable

from src.application.services.job_queue import JobQueue, TaskCancelledError
from src.application.services.orchestrator import PipelineOrchestrator
from src.application.services.request_snapshot import (
    pipeline_request_from_snapshot,
    pipeline_request_to_snapshot,
)
from src.domain.job_state import JobRecord
from src.domain.types import PipelineRequest


class TaskSupervisor:
    """Own and supervise the engine's background task threads.

    The first product release intentionally allows one active pipeline at a
    time. Heavy model loading and GPU memory are not safe to multiplex yet.
    """

    def __init__(self, orchestrator: PipelineOrchestrator, job_queue: JobQueue):
        self.orchestrator = orchestrator
        self.job_queue = job_queue
        self._threads: dict[int, threading.Thread] = {}
        self._lock = threading.RLock()

    def _prune(self) -> None:
        dead = [run_id for run_id, thread in self._threads.items() if not thread.is_alive()]
        for run_id in dead:
            self._threads.pop(run_id, None)

    def active_run_ids(self) -> list[int]:
        with self._lock:
            self._prune()
            return sorted(self._threads)

    def _assert_capacity(self, requested_run_id: int | None = None) -> None:
        active = self.active_run_ids()
        if requested_run_id is not None and requested_run_id in active:
            raise RuntimeError(f"任务 {requested_run_id} 已在运行，不能重复启动。")
        if active:
            raise RuntimeError(
                f"当前已有任务 {active[0]} 正在运行。首个正式版一次只执行一个任务。"
            )

    @staticmethod
    def _legacy_snapshot(job: JobRecord) -> dict:
        output_dir = "./output"
        if job.job_dir:
            # {output_dir}/.jobs/{uuid}
            output_dir = os.path.dirname(os.path.dirname(job.job_dir)) or output_dir
        request = PipelineRequest(input=job.input, title=job.title, output_dir=output_dir)
        return pipeline_request_to_snapshot(request)

    def _request_for_job(self, job: JobRecord) -> PipelineRequest:
        snapshot = job.request_snapshot or self._legacy_snapshot(job)
        return pipeline_request_from_snapshot(snapshot)

    def _spawn(self, run_id: int, request: PipelineRequest, *, force: bool = False) -> None:
        self._assert_capacity(run_id)
        token = self.job_queue.get_token(run_id) or self.job_queue.create_token(run_id)

        def _worker() -> None:
            try:
                result = self.orchestrator.run(
                    request=request,
                    resume_run_id=run_id,
                    job_queue=self.job_queue,
                    cancel_token=token,
                    force=force,
                )
                self.job_queue.complete(
                    run_id,
                    notes_path=result.notes_path,
                    transcript_path=result.transcript_path,
                    elapsed_sec=result.elapsed_sec,
                    frames_count=result.frames_count,
                    note_id=result.note_id,
                )
            except TaskCancelledError:
                # Orchestrator finalizes pause/cancel after a safe stage exit.
                pass
            except Exception as exc:
                self.job_queue.fail(run_id, str(exc))
            finally:
                with self._lock:
                    self._threads.pop(run_id, None)

        thread = threading.Thread(
            target=_worker,
            name=f"video-notes-job-{run_id}",
            daemon=True,
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()

    def start(self, request: PipelineRequest) -> int:
        self._assert_capacity()
        snapshot = pipeline_request_to_snapshot(request)
        run_id = self.job_queue.enqueue(
            input_path=request.input,
            title=request.title,
            request_snapshot=snapshot,
        )
        self._spawn(run_id, request)
        return run_id

    def resume(self, run_id: int) -> int:
        self._assert_capacity(run_id)
        job = self.job_queue.get_job(run_id)
        if job is None:
            raise ValueError(f"任务不存在: {run_id}")
        if not job.can_resume:
            raise ValueError(f"任务 {run_id} 当前状态不可继续: {job.status}")
        refreshed = self.job_queue.prepare_resume(run_id)
        self._spawn(run_id, self._request_for_job(refreshed))
        return run_id

    def retry(self, run_id: int) -> int:
        self._assert_capacity()
        source = self.job_queue.get_job(run_id)
        if source is None:
            raise ValueError(f"任务不存在: {run_id}")
        if source.status not in {"failed", "interrupted", "cancelled"}:
            raise ValueError(f"任务 {run_id} 当前状态不可从头重跑: {source.status}")

        request = self._request_for_job(source)
        snapshot = pipeline_request_to_snapshot(request)
        new_run_id = self.job_queue.enqueue(
            input_path=request.input,
            title=request.title,
            request_snapshot=snapshot,
            parent_run_id=source.id,
            attempt=source.attempt + 1,
        )
        self._spawn(new_run_id, request, force=True)
        return new_run_id
