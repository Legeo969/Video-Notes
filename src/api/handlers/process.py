"""Versioned ``process.*`` RPC handlers.

Handlers validate API input and delegate all worker ownership to
:class:`TaskSupervisor`. They never create background threads directly.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from src.api.event_journal import EventJournal
from src.api.protocol.errors import InvalidParams, JobNotFound
from src.application.services.job_queue import JobQueue, get_default_db_path
from src.application.services.orchestrator import PipelineOrchestrator
from src.application.services.task_supervisor import TaskSupervisor
from src.config.settings import (
    get_default_export_dir,
    get_settings_path,
    load_settings,
    resolve_provider_binding_from_settings,
)
from src.domain.types import PipelineRequest


def _run_id(params: dict[str, Any]) -> int:
    value = params.get("job_id", params.get("id"))
    if value is None:
        raise InvalidParams("job_id is required")
    try:
        return int(value)
    except (ValueError, TypeError) as exc:
        raise InvalidParams("job_id must be an integer") from exc


def _job_record_to_dict(record) -> dict[str, Any]:
    return {
        "id": record.id,
        "job_id": record.job_id,
        "title": record.title,
        "input": record.input,
        "status": record.status,
        "stage": record.stage,
        "last_active_stage": record.last_active_stage,
        "progress": float(record.progress or 0.0),
        "progress_message": record.progress_message,
        "created_at": record.started_at,
        "completed_at": record.completed_at,
        "elapsed_sec": record.elapsed_sec,
        "error_message": record.error_message,
        "output_path": record.output_path,
        "transcript_path": record.transcript_path,
        "frames_count": record.frames_count,
        "note_id": record.note_id,
        "attempt": record.attempt,
        "parent_run_id": record.parent_run_id,
        "can_resume": record.can_resume,
        "heartbeat_at": record.heartbeat_at,
        "interrupted_at": record.interrupted_at,
    }


def _build_request(params: dict[str, Any]) -> PipelineRequest:
    input_src = str(params.get("input") or "").strip()
    if not input_src:
        raise InvalidParams("input is required")

    settings = load_settings(get_settings_path())
    llm = resolve_provider_binding_from_settings(settings, "llm")
    vision = resolve_provider_binding_from_settings(settings, "vision")

    def value(name: str, default=None):
        supplied = params.get(name)
        return supplied if supplied is not None else default

    vault_path = str(value("vault_path", settings.get("vault_path")) or "").strip() or None

    request = PipelineRequest(
        input=input_src,
        title=value("title"),
        transcription_backend=value("transcription_backend", settings.get("transcription_backend", "whisper_cpp")),
        whisper_model=value("whisper_model", settings.get("whisper_model", "large-v3")),
        language=value("language"),
        output_dir=value("output_dir", settings.get("output_dir") or get_default_export_dir()),
        model_dir=value("model_dir", settings.get("whisper_model_dir") or settings.get("model_dir")),
        whisper_device=value("whisper_device", settings.get("whisper_device", "auto")),
        whisper_compute_type=value("whisper_compute_type", settings.get("whisper_compute_type", "auto")),
        beam_size=value("beam_size", 5),
        vad_filter=value("vad_filter", False),
        gpt_model=value("gpt_model", llm.get("model") or "mimo-v2.5"),
        temperature=value("temperature", 0.3),
        style=value("style"),
        template=value("template"),
        template_id=value("template_id"),
        vision_enabled=value("vision_enabled", settings.get("vision_enabled", False)),
        vision_provider=value("vision_provider", vision.get("type") or None),
        vision_model=value("vision_model", vision.get("model") or None),
        vision_api_key=value("vision_api_key", vision.get("api_key") or None),
        vision_base_url=value("vision_base_url", vision.get("base_url") or None),
        ocr_enabled=value("ocr_enabled", settings.get("ocr_enabled", False)),
        ocr_backend=value("ocr_backend", settings.get("ocr_backend", "tesseract")),
        subtitle_format=value("subtitle_format", settings.get("subtitle_format", "none")),
        collection_id=value("collection_id"),
        frame_interval=value("frame_interval", settings.get("frame_interval", 30)),
        frame_mode=value("frame_mode", settings.get("frame_mode", "fixed")),
        max_frames=value("max_frames", settings.get("max_frames", 30)),
        smart_summary=value("smart_summary", False),
        map_max_workers=value("map_max_workers", 6),
        provider=value("provider", llm.get("type") or None),
        api_key=value("api_key", llm.get("api_key") or None),
        base_url=value("base_url", llm.get("base_url") or None),
        vault_path=vault_path,
        bilibili_cookies=value("bilibili_cookies", settings.get("bilibili_cookies") or settings.get("bilibili_cookie_file")),
        export_mode=value("export_mode", settings.get("export_mode", "clean")),
        artifact_layout=value("artifact_layout", "versioned"),
    )
    object.__setattr__(request, "_llm_profile_name", str(llm.get("name") or ""))
    object.__setattr__(request, "_vision_profile_name", str(vision.get("name") or ""))
    return request


def create_process_handlers(
    orchestrator: PipelineOrchestrator | None,
    job_queue: JobQueue | None,
    journal: EventJournal | None = None,
    supervisor: TaskSupervisor | None = None,
) -> dict[str, Any]:
    """Create the process API surface with one shared runtime owner."""
    orchestrator = orchestrator or PipelineOrchestrator()
    job_queue = job_queue or JobQueue(
        db_path=get_default_db_path(get_default_export_dir()),
        output_dir=get_default_export_dir(),
    )
    supervisor = supervisor or TaskSupervisor(orchestrator, job_queue)

    def get_job(run_id: int):
        job = job_queue.get_job(run_id)
        if job is None:
            raise JobNotFound(run_id)
        return job

    def handle_start(params: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"job_id": supervisor.start(_build_request(params))}
        except RuntimeError as exc:
            raise InvalidParams(str(exc)) from exc

    def handle_pause(params: dict[str, Any]) -> bool:
        return job_queue.pause_task(_run_id(params))

    def handle_cancel(params: dict[str, Any]) -> bool:
        return job_queue.cancel_task(_run_id(params))

    def handle_resume(params: dict[str, Any]) -> dict[str, Any]:
        run_id = _run_id(params)
        get_job(run_id)
        try:
            return {"job_id": supervisor.resume(run_id)}
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            raise InvalidParams(str(exc)) from exc

    def handle_retry(params: dict[str, Any]) -> dict[str, Any]:
        run_id = _run_id(params)
        get_job(run_id)
        try:
            return {"job_id": supervisor.retry(run_id)}
        except (ValueError, RuntimeError) as exc:
            raise InvalidParams(str(exc)) from exc

    def handle_list(params: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            limit = max(1, min(500, int(params.get("limit", 50))))
            offset = max(0, int(params.get("offset", 0)))
        except (TypeError, ValueError) as exc:
            raise InvalidParams("limit and offset must be integers") from exc
        records = job_queue.list_jobs(
            limit=limit, offset=offset, status=params.get("status")
        )
        return [_job_record_to_dict(record) for record in records]

    def handle_get(params: dict[str, Any]) -> dict[str, Any]:
        return _job_record_to_dict(get_job(_run_id(params)))

    def handle_events(params: dict[str, Any]) -> list[dict[str, Any]]:
        if journal is None:
            return []
        run_id = _run_id(params)
        get_job(run_id)
        return journal.events_since(run_id, int(params.get("after_id", 0) or 0))

    def handle_open_output(params: dict[str, Any]) -> str:
        job = get_job(_run_id(params))
        path = str(job.output_path or job.job_dir or "").strip()
        if not path:
            raise InvalidParams("job has no output path")
        target = Path(path)
        if not target.exists():
            raise InvalidParams(f"output path does not exist: {path}")
        open_target = target if target.is_dir() else target.parent
        try:
            os.startfile(str(open_target))  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.Popen(["open", str(open_target)])
        except OSError as exc:
            raise InvalidParams(str(exc)) from exc
        return str(open_target)

    def handle_delete(params: dict[str, Any]) -> bool:
        try:
            return job_queue.delete_job(_run_id(params))
        except RuntimeError as exc:
            raise InvalidParams(str(exc)) from exc

    def handle_permanent_clean(params: dict[str, Any]) -> dict[str, Any]:
        return dict(job_queue.purge_hidden_history())

    return {
        "process.start": handle_start,
        "process.pause": handle_pause,
        "process.cancel": handle_cancel,
        "process.resume": handle_resume,
        "process.retry": handle_retry,
        "process.list": handle_list,
        "process.get": handle_get,
        "process.events": handle_events,
        "process.events_since": handle_events,
        "process.open_output": handle_open_output,
        "process.delete": handle_delete,
        "process.permanent_clean": handle_permanent_clean,
    }
