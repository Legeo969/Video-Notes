"""Enhanced pipeline with logging and processing metadata.

Wraps the core pipeline functions to add:
- Structured logging via logging module
- Processing metadata recording to SQLite
- Error handling with stage context
"""

from __future__ import annotations

import os
import time
from typing import Optional

from src.utils.logging import get_logger
from src.infrastructure.db.processing_metadata import ProcessingMetadata
from src.application.pipeline.video_pipeline import process_url as _process_url, process_local as _process_local

logger = get_logger(__name__)

def _get_metadata_db(output_dir: str) -> ProcessingMetadata:
    """Get or create ProcessingMetadata instance for the output directory."""
    db_path = os.path.join(output_dir, "processing.db")
    return ProcessingMetadata(db_path)


def process_url(
    url: str,
    whisper_model: str = "large-v3",
    output_dir: str = "./output",
    title: str = None,
    language: str = None,
    gpt_model: str = "mimo-v2.5",
    api_key: str | None = None,
    base_url: str | None = None,
    model_dir: str | None = None,
    frame_interval: int = 30,
    vault_path: str | None = None,
    template: str | None = None,
    subtitle_format: str = "none",
    temperature: float = 0.3,
    style: str | None = None,
    smart_summary: bool = False,
    provider: str | None = None,
    vision_enabled: bool = False,
    ocr_enabled: bool = False,
    vision_provider: str | None = None,
    vision_model: str | None = None,
    vision_api_key: str | None = None,
    vision_base_url: str | None = None,
    template_id: str | None = None,
) -> str:
    """Process online video URL with logging and metadata recording.

    Args:
        url: Video URL to process.
        whisper_model: Whisper model size.
        output_dir: Output directory.
        title: Optional video title.
        language: Optional language code.
        gpt_model: GPT model name.
        api_key: Optional API key.
        base_url: Optional API base URL.
        model_dir: Optional model directory.
        frame_interval: Frame extraction interval.
        vault_path: Optional Obsidian vault path.
        template: Optional template path.
        subtitle_format: Subtitle format (srt/ass/txt/none).
        temperature: AI temperature parameter.
        style: Optional note style.
        smart_summary: Enable smart summary for multi-chunk notes.

    Returns:
        Path to the generated notes file.
    """
    logger.info(f"🚀 开始处理在线视频: {url}")

    # Record processing start
    metadata = _get_metadata_db(output_dir)
    run_id = metadata.start_run(input_path=url, title=title)

    start_time = time.time()
    try:
        notes_path = _process_url(
            url=url,
            whisper_model=whisper_model,
            output_dir=output_dir,
            title=title,
            language=language,
            gpt_model=gpt_model,
            api_key=api_key,
            base_url=base_url,
            model_dir=model_dir,
            frame_interval=frame_interval,
            vault_path=vault_path,
            template=template,
            template_id=template_id,
            subtitle_format=subtitle_format,
            temperature=temperature,
            style=style,
            smart_summary=smart_summary,
            provider=provider,
            vision_enabled=vision_enabled,
            ocr_enabled=ocr_enabled,
            vision_provider=vision_provider,
            vision_model=vision_model,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
        )

        elapsed = time.time() - start_time
        logger.info(f"✅ 处理完成: {notes_path} (耗时 {elapsed:.1f}s)")

        # Record success
        metadata.complete_run(run_id, output_path=notes_path)

        return notes_path

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ 处理失败: {e} (耗时 {elapsed:.1f}s)")

        # Record failure
        metadata.fail_run(run_id, str(e))
        raise


def process_local(
    file_path: str,
    whisper_model: str = "large-v3",
    output_dir: str = "./output",
    title: str = None,
    language: str = None,
    gpt_model: str = "mimo-v2.5",
    api_key: str | None = None,
    base_url: str | None = None,
    model_dir: str | None = None,
    frame_interval: int = 30,
    vault_path: str | None = None,
    template: str | None = None,
    subtitle_format: str = "none",
    temperature: float = 0.3,
    style: str | None = None,
    smart_summary: bool = False,
    provider: str | None = None,
    vision_enabled: bool = False,
    ocr_enabled: bool = False,
    vision_provider: str | None = None,
    vision_model: str | None = None,
    vision_api_key: str | None = None,
    vision_base_url: str | None = None,
    template_id: str | None = None,
) -> str:
    """Process local video file with logging and metadata recording.

    Args:
        file_path: Path to local video file.
        whisper_model: Whisper model size.
        output_dir: Output directory.
        title: Optional video title.
        language: Optional language code.
        gpt_model: GPT model name.
        api_key: Optional API key.
        base_url: Optional API base URL.
        model_dir: Optional model directory.
        frame_interval: Frame extraction interval.
        vault_path: Optional Obsidian vault path.
        template: Optional template path.
        subtitle_format: Subtitle format (srt/ass/txt/none).
        temperature: AI temperature parameter.
        style: Optional note style.
        smart_summary: Enable smart summary for multi-chunk notes.

    Returns:
        Path to the generated notes file.
    """
    logger.info(f"🚀 开始处理本地文件: {file_path}")

    # Record processing start
    metadata = _get_metadata_db(output_dir)
    run_id = metadata.start_run(input_path=file_path, title=title)

    start_time = time.time()
    try:
        notes_path = _process_local(
            file_path=file_path,
            whisper_model=whisper_model,
            output_dir=output_dir,
            title=title,
            language=language,
            gpt_model=gpt_model,
            api_key=api_key,
            base_url=base_url,
            model_dir=model_dir,
            frame_interval=frame_interval,
            vault_path=vault_path,
            template=template,
            template_id=template_id,
            subtitle_format=subtitle_format,
            temperature=temperature,
            style=style,
            smart_summary=smart_summary,
            provider=provider,
            vision_enabled=vision_enabled,
            ocr_enabled=ocr_enabled,
            vision_provider=vision_provider,
            vision_model=vision_model,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
        )

        elapsed = time.time() - start_time
        logger.info(f"✅ 处理完成: {notes_path} (耗时 {elapsed:.1f}s)")

        # Record success
        metadata.complete_run(run_id, output_path=notes_path)

        return notes_path

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ 处理失败: {e} (耗时 {elapsed:.1f}s)")

        # Record failure
        metadata.fail_run(run_id, str(e))
        raise