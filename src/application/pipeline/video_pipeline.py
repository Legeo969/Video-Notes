"""视频处理管线 - 单视频处理流程

本模块是向后兼容层。核心实现已迁移到 src.application.services.* 服务层。

process_url / process_local 委托给 PipelineOrchestrator，
保持与旧代码完全相同的函数签名和返回行为。

Compatibility Contract:
- process_url(url, ...) -> str — 返回笔记路径（保持不变）
- process_local(file_path, ...) -> str — 返回笔记路径（保持不变）
- _run_pipeline(request) -> str — 内部方法，参数签名已变
- _save_transcript_and_notes — 已移除（V1.0），由 ArtifactWriter / PipelineOrchestrator 取代

v0.3 新增：
- resume_run_id 参数支持断点续跑
- job_queue 参数支持任务状态持久化
"""

import os
import time
from typing import Optional

from src.domain.types import PipelineRequest
from src.application.services.orchestrator import PipelineOrchestrator
from src.application.services.job_queue import (
    JobQueue, CancellationToken, TaskCancelledError, get_default_db_path,
)


def _build_request(
    *,
    url: str | None = None,
    file_path: str | None = None,
    whisper_model: str = "large-v3",
    output_dir: str = "./output",
    title: str | None = None,
    language: str | None = None,
    gpt_model: str = "mimo-v2.5",
    api_key: str | None = None,
    base_url: str | None = None,
    model_dir: str | None = None,
    frame_interval: int = 30,
    frame_mode: str = "fixed",
    max_frames: int = 30,
    vault_path: str | None = None,
    template: str | None = None,
    template_id: str | None = None,
    subtitle_format: str = "none",
    temperature: float = 0.3,
    style: str | None = None,
    smart_summary: bool = False,
    with_citations: bool = False,
    blocks: bool = True,
    provider: str | None = None,
    vision_enabled: bool = False,
    ocr_enabled: bool = False,
    vision_provider: str | None = None,
    vision_model: str | None = None,
    vision_api_key: str | None = None,
    vision_base_url: str | None = None,
    collection_id: str | None = None,
) -> PipelineRequest:
    """从散乱的 kwargs 构建 PipelineRequest。"""
    return PipelineRequest(
        input=url or file_path or "",
        collection_id=collection_id,
        output_dir=output_dir,
        title=title,
        language=language,
        whisper_model=whisper_model,
        model_dir=model_dir,
        gpt_model=gpt_model,
        api_key=api_key,
        base_url=base_url,
        provider=provider,
        template=template,
        template_id=template_id,
        temperature=temperature,
        style=style,
        smart_summary=smart_summary,
        frame_interval=frame_interval,
        frame_mode=frame_mode,
        max_frames=max_frames,
        vision_enabled=vision_enabled,
        vision_provider=vision_provider,
        vision_model=vision_model,
        vision_api_key=vision_api_key,
        vision_base_url=vision_base_url,
        ocr_enabled=ocr_enabled,
        subtitle_format=subtitle_format,
        vault_path=vault_path,
        blocks=blocks,
    )


def process_url(
    url: str,
    whisper_model: str = "large-v3",
    output_dir: str = "./output",
    title: str | None = None,
    language: str | None = None,
    gpt_model: str = "mimo-v2.5",
    api_key: str | None = None,
    base_url: str | None = None,
    model_dir: str | None = None,
    frame_interval: int = 30,
    frame_mode: str = "fixed",
    max_frames: int = 30,
    vault_path: str | None = None,
    template: str | None = None,
    subtitle_format: str = "none",
    temperature: float = 0.3,
    style: str | None = None,
    smart_summary: bool = False,
    with_citations: bool = False,
    plugin_manager=None,
    blocks: bool = True,
    provider: str | None = None,
    vision_enabled: bool = False,
    ocr_enabled: bool = False,
    vision_provider: str | None = None,
    vision_model: str | None = None,
    vision_api_key: str | None = None,
    vision_base_url: str | None = None,
    resume_run_id: int | None = None,
    job_queue: JobQueue | None = None,
    cancel_token: CancellationToken | None = None,
    force: bool = False,
    collection_id: str | None = None,
    template_id: str | None = None,
) -> str:
    """在线视频流程：URL → 音频/视频 → 转录 → 笔记

    此函数委托给 PipelineOrchestrator，保持向后兼容。
    """
    request = _build_request(
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
        frame_mode=frame_mode,
        max_frames=max_frames,
        vault_path=vault_path,
        template=template,
        template_id=template_id,
        subtitle_format=subtitle_format,
        temperature=temperature,
        style=style,
        smart_summary=smart_summary,
        with_citations=with_citations,
        blocks=blocks,
        provider=provider,
        vision_enabled=vision_enabled,
        ocr_enabled=ocr_enabled,
        vision_provider=vision_provider,
        vision_model=vision_model,
        vision_api_key=vision_api_key,
        vision_base_url=vision_base_url,
        collection_id=collection_id,
    )

    orchestrator = PipelineOrchestrator()

    # Auto-create JobQueue if not provided (for task history)
    if job_queue is None and resume_run_id is not None:
        db_path = get_default_db_path(output_dir)
        job_queue = JobQueue(db_path, output_dir=output_dir)

    try:
        result = orchestrator.run(
            request,
            plugin_manager=plugin_manager,
            resume_run_id=resume_run_id,
            job_queue=job_queue,
            cancel_token=cancel_token,
            force=force,
        )
    except TaskCancelledError as exc:
        if job_queue and resume_run_id:
            current = job_queue.get_job(resume_run_id)
            status = current.status if current else None
            if getattr(exc, "action", "pause") == "pause":
                if status != "paused":
                    job_queue.pause_task(resume_run_id)
            else:
                if status != "cancelled":
                    job_queue.cancel_task(resume_run_id)
        raise
    except Exception as exc:
        if job_queue and resume_run_id:
            job_queue.fail(resume_run_id, str(exc))
        raise

    if job_queue and resume_run_id:
        job_queue.complete(
            resume_run_id,
            notes_path=result.notes_path,
            transcript_path=result.transcript_path,
            elapsed_sec=result.elapsed_sec,
            frames_count=result.frames_count,
            note_id=result.note_id,
        )

    return result.notes_path


def process_local(
    file_path: str,
    whisper_model: str = "large-v3",
    output_dir: str = "./output",
    title: str | None = None,
    language: str | None = None,
    gpt_model: str = "mimo-v2.5",
    api_key: str | None = None,
    base_url: str | None = None,
    model_dir: str | None = None,
    frame_interval: int = 30,
    frame_mode: str = "fixed",
    max_frames: int = 30,
    vault_path: str | None = None,
    template: str | None = None,
    subtitle_format: str = "none",
    temperature: float = 0.3,
    style: str | None = None,
    smart_summary: bool = False,
    with_citations: bool = False,
    plugin_manager=None,
    blocks: bool = True,
    provider: str | None = None,
    vision_enabled: bool = False,
    ocr_enabled: bool = False,
    vision_provider: str | None = None,
    vision_model: str | None = None,
    vision_api_key: str | None = None,
    vision_base_url: str | None = None,
    resume_run_id: int | None = None,
    job_queue: JobQueue | None = None,
    cancel_token: CancellationToken | None = None,
    force: bool = False,
    collection_id: str | None = None,
    template_id: str | None = None,
) -> str:
    """本地文件流程：视频 → 提取音频 + 截帧 → 转录 → 笔记（带截图）

    此函数委托给 PipelineOrchestrator，保持向后兼容。
    """
    request = _build_request(
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
        frame_mode=frame_mode,
        max_frames=max_frames,
        vault_path=vault_path,
        template=template,
        template_id=template_id,
        subtitle_format=subtitle_format,
        temperature=temperature,
        style=style,
        smart_summary=smart_summary,
        with_citations=with_citations,
        blocks=blocks,
        provider=provider,
        vision_enabled=vision_enabled,
        ocr_enabled=ocr_enabled,
        vision_provider=vision_provider,
        vision_model=vision_model,
        vision_api_key=vision_api_key,
        vision_base_url=vision_base_url,
        collection_id=collection_id,
    )

    orchestrator = PipelineOrchestrator()

    # Auto-create JobQueue if not provided (for task history)
    if job_queue is None and resume_run_id is not None:
        db_path = get_default_db_path(output_dir)
        job_queue = JobQueue(db_path, output_dir=output_dir)

    try:
        result = orchestrator.run(
            request,
            plugin_manager=plugin_manager,
            resume_run_id=resume_run_id,
            job_queue=job_queue,
            cancel_token=cancel_token,
            force=force,
        )
    except TaskCancelledError as exc:
        if job_queue and resume_run_id:
            current = job_queue.get_job(resume_run_id)
            status = current.status if current else None
            if getattr(exc, "action", "pause") == "pause":
                if status != "paused":
                    job_queue.pause_task(resume_run_id)
            else:
                if status != "cancelled":
                    job_queue.cancel_task(resume_run_id)
        raise
    except Exception as exc:
        if job_queue and resume_run_id:
            job_queue.fail(resume_run_id, str(exc))
        raise

    if job_queue and resume_run_id:
        job_queue.complete(
            resume_run_id,
            notes_path=result.notes_path,
            transcript_path=result.transcript_path,
            elapsed_sec=result.elapsed_sec,
            frames_count=result.frames_count,
            note_id=result.note_id,
        )

    return result.notes_path
