"""process.* RPC 处理器

封装 PipelineOrchestrator + JobQueue 对外提供任务生命周期管理。
处理器不实现管线逻辑，只做参数校验和委托调用。
"""

from __future__ import annotations

import os
import logging
from typing import Any

from src.domain.types import PipelineRequest
from src.domain.job_state import JobState
from src.application.services.orchestrator import PipelineOrchestrator
from src.application.services.job_queue import (
    JobQueue,
    get_default_db_path,
    TaskCancelledError,
)
from src.api.protocol.errors import JobNotFound, InternalError, InvalidParams

logger = logging.getLogger(__name__)


def _job_record_to_dict(record) -> dict[str, Any]:
    """将 JobRecord 转为可序列化的 dict（不暴露内部 dataclass）。"""
    import dataclasses
    raw = dataclasses.asdict(record)
    return {
        "id": raw["id"],
        "job_id": raw["job_id"],
        "title": raw.get("title"),
        "input": raw["input"],
        "status": raw["status"],
        "stage": raw["stage"],
        "progress": 0.0,  # 可由 event_journal 补充
        "created_at": raw.get("started_at"),
        "completed_at": raw.get("completed_at"),
        "elapsed_sec": raw.get("elapsed_sec", 0.0),
        "error_message": raw.get("error_message"),
        "output_path": raw.get("output_path"),
        "transcript_path": raw.get("transcript_path"),
        "frames_count": raw.get("frames_count", 0),
        "note_id": raw.get("note_id"),
    }


def create_process_handlers(
    orchestrator: PipelineOrchestrator,
    job_queue: JobQueue,
) -> dict[str, Any]:
    """创建 process.* 方法处理器字典。"""

    # ── 内部辅助 ──

    def _get_job_or_error(run_id: int):
        job = job_queue.get_job(run_id)
        if job is None:
            raise JobNotFound(run_id)
        return job

    # ── 处理器 ──

    def handle_start(params: dict[str, Any]) -> dict[str, Any]:
        """process.start — 启动新管线任务。"""
        input_src = params.get("input", "").strip()
        if not input_src:
            raise InvalidParams("input is required")

        # 入队
        title = params.get("title")
        run_id = job_queue.enqueue(input_path=input_src, title=title)

        # 从 params 构建 PipelineRequest
        request = PipelineRequest(
            input=input_src,
            title=title,
            whisper_model=params.get("whisper_model", "large-v3"),
            language=params.get("language"),
            output_dir=params.get("output_dir", "./output"),
            model_dir=params.get("model_dir"),
            beam_size=params.get("beam_size", 5),
            vad_filter=params.get("vad_filter", False),
            gpt_model=params.get("gpt_model", "mimo-v2.5"),
            temperature=params.get("temperature", 0.3),
            style=params.get("style"),
            template=params.get("template"),
            template_id=params.get("template_id"),
            vision_enabled=params.get("vision_enabled", False),
            vision_provider=params.get("vision_provider"),
            vision_model=params.get("vision_model"),
            ocr_enabled=params.get("ocr_enabled", False),
            subtitle_format=params.get("subtitle_format", "none"),
            collection_id=params.get("collection_id"),
            frame_interval=params.get("frame_interval", 30),
            frame_mode=params.get("frame_mode", "fixed"),
            max_frames=params.get("max_frames", 30),
            smart_summary=params.get("smart_summary", False),
            map_max_workers=params.get("map_max_workers", 6),
            provider=params.get("provider"),
            api_key=params.get("api_key"),
            base_url=params.get("base_url"),
        )

        # 在后台线程中运行管线
        import threading

        token = job_queue.create_token(run_id)

        def _run():
            try:
                result = orchestrator.run(
                    request=request,
                    resume_run_id=run_id,
                    job_queue=job_queue,
                    cancel_token=token,
                )
                job_queue.complete(
                    run_id,
                    notes_path=result.notes_path,
                    transcript_path=result.transcript_path,
                    elapsed_sec=result.elapsed_sec,
                    frames_count=result.frames_count,
                    note_id=result.note_id,
                )
            except TaskCancelledError:
                pass  # job_queue.finalize_stop already called inside orchestrator
            except Exception as exc:
                logger.exception("Pipeline failed for run_id=%s", run_id)
                job_queue.fail(run_id, str(exc))

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        return {"job_id": run_id}

    def handle_pause(params: dict[str, Any]) -> bool:
        """process.pause — 暂停运行中的任务。"""
        run_id = params.get("job_id")
        if run_id is None:
            raise InvalidParams("job_id is required")
        try:
            run_id = int(run_id)
        except (ValueError, TypeError):
            raise InvalidParams("job_id must be an integer")
        return job_queue.pause_task(run_id)

    def handle_cancel(params: dict[str, Any]) -> bool:
        """process.cancel — 取消任务。"""
        run_id = params.get("job_id")
        if run_id is None:
            raise InvalidParams("job_id is required")
        try:
            run_id = int(run_id)
        except (ValueError, TypeError):
            raise InvalidParams("job_id must be an integer")
        return job_queue.cancel_task(run_id)

    def handle_resume(params: dict[str, Any]) -> dict[str, Any]:
        """process.resume — 恢复暂停/失败的任务。"""
        run_id = params.get("job_id")
        if run_id is None:
            raise InvalidParams("job_id is required")
        try:
            run_id = int(run_id)
        except (ValueError, TypeError):
            raise InvalidParams("job_id must be an integer")

        job = _get_job_or_error(run_id)
        if not job.can_resume:
            raise InvalidParams(f"Job {run_id} cannot be resumed (status={job.status})")

        # 准备断点续跑
        refreshed = job_queue.prepare_resume(run_id)

        # 构建请求
        request = PipelineRequest(
            input=refreshed.input,
            title=refreshed.title,
            output_dir=os.path.dirname(
                os.path.dirname(os.path.dirname(refreshed.job_dir or ""))
            ) or "./output",
        )

        token = job_queue.get_token(run_id) or job_queue.create_token(run_id)

        import threading

        def _run():
            try:
                result = orchestrator.run(
                    request=request,
                    resume_run_id=run_id,
                    job_queue=job_queue,
                    cancel_token=token,
                )
                job_queue.complete(
                    run_id,
                    notes_path=result.notes_path,
                    transcript_path=result.transcript_path,
                    elapsed_sec=result.elapsed_sec,
                    frames_count=result.frames_count,
                    note_id=result.note_id,
                )
            except TaskCancelledError:
                pass
            except Exception as exc:
                logger.exception("Resume failed for run_id=%s", run_id)
                job_queue.fail(run_id, str(exc))

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        return {"job_id": run_id}

    def handle_retry(params: dict[str, Any]) -> dict[str, Any]:
        """process.retry — 从头重跑失败的任务。"""
        # 逻辑与 resume 相同，但强制 force=True
        run_id = params.get("job_id")
        if run_id is None:
            raise InvalidParams("job_id is required")
        try:
            run_id = int(run_id)
        except (ValueError, TypeError):
            raise InvalidParams("job_id must be an integer")

        job = _get_job_or_error(run_id)
        if not job.is_failed and not job.is_cancelled:
            raise InvalidParams(
                f"Job {run_id} cannot be retried (status={job.status})"
            )

        # 重建请求
        request = PipelineRequest(
            input=job.input,
            title=job.title,
            output_dir=os.path.dirname(
                os.path.dirname(os.path.dirname(job.job_dir or ""))
            ) or "./output",
        )

        # 入队新任务（从头开始用新 run_id）
        new_run_id = job_queue.enqueue(input_path=job.input, title=job.title)
        token = job_queue.create_token(new_run_id)

        import threading

        def _run():
            try:
                # force=True 忽略已有产物
                result = orchestrator.run(
                    request=request,
                    resume_run_id=new_run_id,
                    job_queue=job_queue,
                    cancel_token=token,
                    force=True,
                )
                job_queue.complete(
                    new_run_id,
                    notes_path=result.notes_path,
                    transcript_path=result.transcript_path,
                    elapsed_sec=result.elapsed_sec,
                    frames_count=result.frames_count,
                    note_id=result.note_id,
                )
            except TaskCancelledError:
                pass
            except Exception as exc:
                logger.exception("Retry failed for run_id=%s", new_run_id)
                job_queue.fail(new_run_id, str(exc))

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        return {"job_id": new_run_id}

    def handle_list(params: dict[str, Any]) -> list[dict[str, Any]]:
        """process.list — 列出所有任务。"""
        limit = params.get("limit", 50)
        offset = params.get("offset", 0)
        status = params.get("status")
        records = job_queue.list_jobs(limit=limit, offset=offset, status=status)
        return [_job_record_to_dict(r) for r in records]

    def handle_get(params: dict[str, Any]) -> dict[str, Any]:
        """process.get — 获取单任务详情。"""
        run_id = params.get("job_id")
        if run_id is None:
            raise InvalidParams("job_id is required")
        try:
            run_id = int(run_id)
        except (ValueError, TypeError):
            raise InvalidParams("job_id must be an integer")
        job = _get_job_or_error(run_id)
        return _job_record_to_dict(job)

    def handle_delete(params: dict[str, Any]) -> bool:
        """process.delete — 删除任务记录。"""
        run_id = params.get("job_id")
        if run_id is None:
            raise InvalidParams("job_id is required")
        try:
            run_id = int(run_id)
        except (ValueError, TypeError):
            raise InvalidParams("job_id must be an integer")
        return job_queue.delete_job(run_id)

    def handle_permanent_clean(params: dict[str, Any]) -> dict[str, Any]:
        """process.permanent_clean — 永久清理隐藏任务数据。"""
        result = job_queue.purge_hidden_history()
        return dict(result)

    return {
        "process.start": handle_start,
        "process.pause": handle_pause,
        "process.cancel": handle_cancel,
        "process.resume": handle_resume,
        "process.retry": handle_retry,
        "process.list": handle_list,
        "process.get": handle_get,
        "process.delete": handle_delete,
        "process.permanent_clean": handle_permanent_clean,
    }
