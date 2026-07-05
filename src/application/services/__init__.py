"""Application services public API.

The package used to import every service eagerly.  That made a lightweight
import such as ``src.application.services.job_queue`` load LLM providers and
created circular imports through the compatibility ``src.core`` namespace.
Exports are now resolved lazily.
"""
from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    # domain/runtime types kept for backwards compatibility
    "PipelineRequest": ("src.domain.types", "PipelineRequest"),
    "PipelineResult": ("src.domain.types", "PipelineResult"),
    "TranscriptionOptions": ("src.domain.types", "TranscriptionOptions"),
    "NoteOptions": ("src.domain.types", "NoteOptions"),
    "FrameOptions": ("src.domain.types", "FrameOptions"),
    "VisionOptions": ("src.domain.types", "VisionOptions"),
    "OutputOptions": ("src.domain.types", "OutputOptions"),
    "RuntimeCapabilities": ("src.utils.runtime", "RuntimeCapabilities"),
    "JobState": ("src.domain.job_state", "JobState"),
    "JobRecord": ("src.domain.job_state", "JobRecord"),
    "StageManifest": ("src.domain.job_state", "StageManifest"),
    "get_stage_order": ("src.domain.job_state", "get_stage_order"),
    "get_stage_artifact": ("src.domain.job_state", "get_stage_artifact"),
    "get_stage_outputs": ("src.domain.job_state", "get_stage_outputs"),
    "artifact_path": ("src.domain.job_state", "artifact_path"),
    "temp_path": ("src.domain.job_state", "temp_path"),
    "PipelineOrchestrator": ("src.application.services.orchestrator", "PipelineOrchestrator"),
    "JobQueue": ("src.application.services.job_queue", "JobQueue"),
    "CancellationToken": ("src.application.services.job_queue", "CancellationToken"),
    "TaskCancelledError": ("src.application.services.job_queue", "TaskCancelledError"),
    "ProcessRegistry": ("src.application.services.job_queue", "ProcessRegistry"),
    "atomic_write_json": ("src.application.services.job_queue", "atomic_write_json"),
    "atomic_write_text": ("src.application.services.job_queue", "atomic_write_text"),
    "get_default_db_path": ("src.application.services.job_queue", "get_default_db_path"),
    "MediaResolver": ("src.application.services.media_resolver", "MediaResolver"),
    "TranscriptionService": ("src.application.services.transcription", "TranscriptionService"),
    "NoteService": ("src.application.services.note_service", "NoteService"),
    "ArtifactWriter": ("src.application.services.artifact_writer", "ArtifactWriter"),
    "CleanupManager": ("src.application.services.cleanup_manager", "CleanupManager"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attr = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attr)
    globals()[name] = value
    return value
