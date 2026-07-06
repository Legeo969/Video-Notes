"""video-notes-ai — AI 视频笔记生成工具.

Public symbols are loaded lazily so lightweight helper processes (for example,
the isolated OCR worker) do not import the full LLM/GUI stack at startup.
"""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "process_url": ("src.application.pipeline.video_pipeline", "process_url"),
    "process_local": ("src.application.pipeline.video_pipeline", "process_local"),
    "generate_notes": ("src.application.notes.note_generator", "generate_notes"),
    "load_template": ("src.application.llm.prompts", "load_template"),
    "build_template_prompt": ("src.application.llm.prompts", "build_template_prompt"),
    "build_user_prompt": ("src.application.llm.prompts", "build_user_prompt"),
    "build_global_summary_prompt": ("src.application.llm.prompts", "build_global_summary_prompt"),
    "transcribe": ("src.infrastructure.transcription.whisper_engine", "transcribe"),
    "transcribe_with_segments": ("src.infrastructure.transcription.whisper_engine", "transcribe_with_segments"),
    "download_audio": ("src.infrastructure.video.downloader", "download_audio"),
    "extract_frames": ("src.infrastructure.video.frame_extractor", "extract_frames"),
    "archive_to_obsidian": ("src.vault_writer", "archive_to_obsidian"),
    "write_srt": ("src.utils.subtitle_writer", "write_srt"),
    "write_ass": ("src.utils.subtitle_writer", "write_ass"),
    "write_timestamped_txt": ("src.utils.subtitle_writer", "write_timestamped_txt"),
    "BatchJob": ("src.application.pipeline.batch_pipeline", "BatchJob"),
    "ProcessingMetadata": ("src.infrastructure.db.processing_metadata", "ProcessingMetadata"),
    "get_logger": ("src.utils.logging", "get_logger"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
