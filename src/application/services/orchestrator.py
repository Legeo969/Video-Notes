"""PipelineOrchestrator — 中央调度器：分阶段执行 + 断点续跑 + 可靠性保障

设计原则（v0.3.1）：
- 每个阶段完成后：原子写入产物 → 保存 manifest → 更新 DB stage
- artifact / temp 两层目录：成功时只清理 temp/，保留 artifacts/
- 断点续跑基于 StageManifest 完整性校验，而非仅靠文件存在
- CancellationToken 在每个阶段前后检查
"""

from __future__ import annotations

import os
import logging

from src.domain.types import PipelineRequest, PipelineResult
from src.domain.job_state import JobState
from src.application.services.job_queue import (
    JobQueue,
    CancellationToken,
    TaskCancelledError,
    get_default_db_path,
)
from src.application.services.cleanup_manager import CleanupManager
from src.application.services.media_resolver import MediaResolver
from src.application.services.transcription import TranscriptionService
from src.application.services.note_service import NoteService
from src.application.services.artifact_writer import ArtifactWriter
from src.application.pipeline.context import ProcessingContext, ProgressCallback
from src.application.pipeline.runner import StageRunner, FileManifestStore


logger = logging.getLogger(__name__)


_PIPELINE_STAGE_TO_JOB_STATE: dict[str, JobState] = {
    "resolve_media": JobState.RESOLVING,
    "transcribe": JobState.TRANSCRIBING,
    "extract_frames": JobState.EXTRACTING_FRAMES,
    "fuse_timeline": JobState.GENERATING_NOTES,
    "vision_analysis": JobState.EXTRACTING_FRAMES,
    "map_notes": JobState.GENERATING_NOTES,
    "reduce_notes": JobState.GENERATING_NOTES,
    "write_artifacts": JobState.INDEXING,
    "index_provenance": JobState.INDEXING,
}


def _job_state_for_progress_stage(stage: str) -> JobState | None:
    mapped = _PIPELINE_STAGE_TO_JOB_STATE.get(stage)
    if mapped is not None:
        return mapped
    try:
        return JobState(stage)
    except ValueError:
        return None


class PipelineOrchestrator:
    """视频处理管线的中心调度器。

    新特性（v0.3.1）：
    - 原子写入中间产物（.tmp → validate → os.replace）
    - 每阶段 StageManifest 完整性校验
    - artifact / temp 分离
    - CancellationToken 取消支持
    - manifest 驱动的断点续跑

    用法：
        orchestrator = PipelineOrchestrator()
        result = orchestrator.run(PipelineRequest(input="url_or_path"))

        # 带取消支持：
        token = CancellationToken()
        result = orchestrator.run(request, cancel_token=token)

        # 恢复：
        result = orchestrator.run(request, resume_run_id=42, job_queue=jq)
    """

    def __init__(self):
        self.media = MediaResolver()
        self.transcription = TranscriptionService()
        self.notes_service = NoteService()
        self.writer = ArtifactWriter()
        self.cleanup = CleanupManager()

    def _cleanup_owned_files(self, ctx: ProcessingContext) -> None:
        """清理当前任务明确拥有的兼容临时路径。

        所有路径都必须位于 ``ctx.job_dir`` 内。过去根据每个文件的父目录
        动态传入 ``job_dir``，会绕过 CleanupManager 的边界保护，并允许
        删除输出根目录中的文件；这里统一使用真实任务目录。
        """
        for path in ctx.owned_files:
            self.cleanup.safe_remove(
                path,
                job_dir=ctx.job_dir,
                label="临时文件",
            )

    # ── 新管线（Vision + MAP/REDUCE） ──

    def _run_new_pipeline(
        self,
        request: PipelineRequest,
        plugin_manager=None,
        resume_run_id: int | None = None,
        job_queue: JobQueue | None = None,
        cancel_token: CancellationToken | None = None,
        force: bool = False,
    ) -> PipelineResult:
        """Vision + MAP/REDUCE 新模式管线。

        流程:
        MediaResolver → SpeechTranscriber → FrameExtractor
        → FrameUnderstandingService → FusionEngine
        → MapStage → ReduceStage → ArtifactWriter
        """
        import time as _time
        from pathlib import Path as _Path

        from src.application.providers.factory import ProviderFactory
        from src.application.pipeline.stages.vision_stage import VisionStage
        from src.application.pipeline.stages.resolve_media import ResolveMediaStage
        from src.application.pipeline.stages.transcribe_stage import TranscribeStage
        from src.application.pipeline.stages.extract_frames_stage import ExtractFramesStage
        from src.application.pipeline.stages.fuse_timeline import FuseTimelineStage
        from src.application.pipeline.stages.map_notes import MapNotesStage
        from src.application.pipeline.stages.reduce_notes import ReduceNotesStage
        from src.application.pipeline.stages.write_artifacts import WriteArtifactsStage
        from src.application.pipeline.stages.index_provenance import IndexProvenanceStage

        t_start = _time.time()
        audio_path = None
        video_path = None
        is_resume = resume_run_id is not None and not force
        job_dir: str | None = None

        # ── 确定任务工作目录 ──
        if is_resume and job_queue:
            job_dir = job_queue.get_job_dir(resume_run_id)
            if not job_dir:
                is_resume = False
            else:
                logger.info(f"📂 恢复任务 #{resume_run_id}，工作目录: {job_dir}")
        if not job_dir:
            job_dir = self.cleanup.create_job_dir(request.output_dir)

        # ── 提取 job_id ──
        job_id = os.path.basename(job_dir.rstrip("/\\"))

        # ── 构建 ProcessingContext（替代 _check / _set_stage 闭包） ──
        _progress_cb: ProgressCallback | None = None
        if job_queue and resume_run_id:
            def _progress(stage: str, msg: str, pct: int) -> None:
                job_state = _job_state_for_progress_stage(stage)
                if job_state is not None:
                    job_queue.save_progress(resume_run_id, job_state, pct, msg)
            _progress_cb = _progress

        ctx = ProcessingContext(
            request=request,
            job_dir=job_dir,
            job_id=job_id,
            resume_run_id=resume_run_id,
            force=force,
            owned_files=[],
            progress=_progress_cb,
            cancel_token=cancel_token,
        )

        _runner = StageRunner(FileManifestStore())

        # ══════════════════════════════
        # 启动配置摘要
        # ══════════════════════════════
        _sep = "═" * 35
        logger.info(_sep)
        # GPU 检测（轻量，不加载模型）
        _gpu = "?"
        try:
            import ctranslate2
            _gpu = "GPU" if ctranslate2.get_cuda_device_count() > 0 else "CPU"
        except Exception as e:
            logger.debug("GPU detection failed: %s", e)
        _wm = request.whisper_model or "large-v3"
        logger.info(f"⚙️  Whisper:  {_wm} [{_gpu}]"
                     f"{' @ ' + request.model_dir if request.model_dir else ''}")
        _fm = request.frame_mode or "auto"
        logger.info(f"📷  截图:      {_fm}"
                     f" (间隔{request.frame_interval}s, 最多{request.max_frames}帧)"
                     if _fm != "disabled" else f"📷  截图:      disabled")
        logger.info(f"👁️  Vision:   {'ON' if request.vision_enabled else 'OFF'}"
                     f"{' (' + request.vision_provider + ')' if request.vision_enabled and request.vision_provider else ''}"
                     f"{' @ ' + request.vision_base_url if request.vision_enabled and request.vision_base_url else ''}"
                     f"{' model=' + request.vision_model if request.vision_enabled and request.vision_model else ''}")
        logger.info(f"🔍  OCR:      {'ON' if request.ocr_enabled else 'OFF'}")
        _pmodel = request.gpt_model or "(默认)"
        logger.info(f"🤖  LLM:       {_pmodel}")
        logger.info(_sep)

        _worker_marked_active = False
        if job_queue is not None and resume_run_id is not None:
            job_queue.mark_worker_active(resume_run_id)
            _worker_marked_active = True

        def _title_fallback():
            """从已下载文件名或 URL 中提取标题（与旧管线逻辑一致）。"""
            import re as _re
            _media_file = video_path or audio_path
            if _media_file:
                _fname = os.path.basename(_media_file)
                _m = _re.match(r'^[A-Za-z0-9]+-(.+?)(?:\.[^.]+)?$', _fname)
                if _m:
                    return _m.group(1).strip()
                return os.path.splitext(_fname)[0]
            if request.input.startswith("http"):
                _url_m = _re.search(r'(BV[a-zA-Z0-9]+)', request.input)
                if _url_m:
                    return _url_m.group(1)
                from urllib.parse import urlparse
                _p = urlparse(request.input)
                return _p.path.rstrip("/").split("/")[-1] or "untitled"
            return os.path.splitext(os.path.basename(request.input))[0] or "untitled"

        try:
            # ══════════════════════════════
            # 阶段 1-4：媒体解析 + 转录 + 帧提取 + 视觉理解
            # ══════════════════════════════
            _io_stages = [
                ResolveMediaStage(media_resolver=self.media),
                TranscribeStage(),
                ExtractFramesStage(),
            ]
            if request.vision_enabled:
                if not request.vision_provider:
                    raise RuntimeError(
                        "视觉 provider 未配置：请在 设置 → 视觉识别 → Provider 中选择"
                    )
                if not request.vision_api_key:
                    raise RuntimeError(
                        "视觉 API Key 未配置：请在 设置 → 视觉识别 → API Key 中填入"
                    )
                vision_provider = ProviderFactory().create(request.vision_llm_config())
                logger.info(f"🔑 视觉 provider: {request.vision_provider}"
                      f"{f' @ {request.vision_base_url}' if request.vision_base_url else ''}"
                      f"  model={request.vision_model or '(默认)'}")
                _io_stages.append(
                    VisionStage(
                        vision_provider=vision_provider,
                        vision_model=request.vision_model,
                        ocr_enabled=request.ocr_enabled,
                    )
                )
            else:
                _io_stages.append(VisionStage())  # No vision provider → skip

            _io_state = _runner.run(ctx, _io_stages)
            audio_path = _io_state["audio_path"]
            video_path = _io_state.get("video_path")
            speech_result = _io_state["speech_result"]
            frames = _io_state["frames"]
            insights = _io_state.get("insights", [])
            logger.info(f"✅ 转录完成: {len(speech_result.segments)} 段, {len(speech_result.full_text)} 字符"
                  f"  语言: {speech_result.language}, 耗时: {speech_result.elapsed:.1f}s")
            flen = len(frames)
            logger.info(f"{'✅ 帧提取完成: ' + str(flen) + ' 帧' if video_path and flen else '⚠️  无视频文件，跳过帧提取'}")

            # Plugin hook: on_transcript
            if plugin_manager:
                try:
                    full_text = speech_result.full_text if speech_result else ""
                    plugin_manager.run_hook("on_transcript", full_text)
                except Exception as e:
                    logger.warning("Plugin on_transcript hook failed: %s", e)

            # ══════════════════════════════
            # 阶段 5-7：融合 + MAP + REDUCE（通过 StageRunner）
            # ══════════════════════════════
            llm_provider = ProviderFactory().create(request.main_llm_config())
            _note_state = _runner.run(ctx, [
                FuseTimelineStage(),
                MapNotesStage(provider=llm_provider, model=request.gpt_model),
                ReduceNotesStage(provider=llm_provider, model=request.gpt_model),
            ], initial_state={"speech_result": speech_result, "insights": insights})
            timeline = _note_state["timeline"]
            chunks = _note_state["chunks"]
            map_results = _note_state["map_results"]
            notes = _note_state["notes"]
            _total_llm = sum(mr.elapsed for mr in map_results)
            logger.info(f"✅ 融合完成: {len(timeline.items)} 时间线项, {len(chunks)} 摘要块")
            logger.info(f"✅ MAP 完成: {len(map_results)} 块, 总 LLM 耗时: {_total_llm:.1f}s")
            logger.info(f"✅ REDUCE 完成: {len(notes)} 字符")

            # Plugin hook: on_note
            if plugin_manager:
                try:
                    metadata = {
                        "title": request.title or "",
                        "input": request.input,
                        "job_id": job_id,
                    }
                    plugin_manager.run_hook("on_note", notes, metadata)
                except Exception as e:
                    logger.warning("Plugin on_note hook failed: %s", e)

            # ══════════════════════════════
            # 标题兜底（在写入产物前确定标题）
            # ══════════════════════════════
            ctx.check_cancelled()
            if not request.title:
                request.title = _title_fallback() or "untitled"

            # ══════════════════════════════
            # 阶段 8-9：产物写入 + Provenance 索引
            # ══════════════════════════════
            _write_state = _runner.run(ctx, [
                WriteArtifactsStage(writer=self.writer),
                IndexProvenanceStage(),
            ], initial_state={
                "speech_result": speech_result,
                "notes": notes,
                "frames": frames,
                "insights": insights,
            })
            transcript_path = _write_state["transcript_path"]
            notes_path = _write_state["notes_path"]
            note_id = _write_state.get("note_id")

            # Plugin hook: on_complete
            if plugin_manager:
                try:
                    plugin_manager.run_hook("on_complete", notes_path)
                except Exception as e:
                    logger.warning("Plugin on_complete hook failed: %s", e)

            # ══════════════════════════════
            # 阶段 11：清理
            # ══════════════════════════════
            self.cleanup.cleanup_temp(job_dir)
            self._cleanup_owned_files(ctx)

            # ══════════════════════════════
            # 阶段 12：归入 Collection
            # ══════════════════════════════
            if request.collection_id and job_id:
                _add_to_collection(
                    request.collection_id,
                    job_id,
                    request.input,
                    request.title,
                    notes_path,
                    request.notes.template_id,
                    request.output_dir,
                )

            # ══════════════════════════════
            # 完成
            # ══════════════════════════════
            elapsed = _time.time() - t_start
            logger.info(f"\n✅ 处理完成! 总耗时: {elapsed:.1f}s")
            logger.info(f"📄 转录: {transcript_path}")
            logger.info(f"📝 笔记: {notes_path}")

            return PipelineResult(
                notes_path=notes_path,
                transcript_path=transcript_path,
                title=request.title,
                input=request.input,
                elapsed_sec=elapsed,
                frames_count=len(frames),
                note_id=note_id,
                job_id=job_id,
            )

        except TaskCancelledError as exc:
            action = getattr(exc, "action", "pause")
            if action == "cancel":
                logger.info("⏹️  任务已取消，正在清理工作目录")
            else:
                logger.info("⏸️  任务已暂停，工作目录和断点已保留，可稍后继续")
            if job_queue is not None and resume_run_id is not None:
                job_queue.finalize_stop(resume_run_id, action)
            elif action == "cancel":
                self.cleanup.cleanup_job(job_dir, label="已取消任务工作目录")
            raise
        except Exception as e:
            elapsed = _time.time() - t_start
            logger.error(f"❌ 处理失败（{elapsed:.0f}s），产物已保留在: {job_dir}")
            if resume_run_id:
                logger.error(f"   可用 --resume {resume_run_id} 恢复")
            # 失败时完整保留 job_dir（包括 temp/），保证断点续跑仍能
            # 访问已下载视频和已提取音频。下次成功或用户删除任务时再清理。
            raise
        finally:
            if _worker_marked_active and job_queue is not None and resume_run_id is not None:
                job_queue.mark_worker_inactive(resume_run_id)

    # ── 主入口 ──

    def run(
        self,
        request: PipelineRequest,
        plugin_manager=None,
        resume_run_id: int | None = None,
        job_queue: JobQueue | None = None,
        cancel_token: CancellationToken | None = None,
        force: bool = False,
    ) -> PipelineResult:
        """执行完整视频处理管线。

        始终使用 Vision + MAP/REDUCE 新模式管线。
        旧模式（pre-V0.9）已被移除。

        Args:
            request: 管线请求参数。
            plugin_manager: 插件管理器（可选）。
            resume_run_id: 断点续跑的任务 ID（None = 全新任务）。
            job_queue: 任务队列管理器（用于状态更新和 manifest 管理）。
            cancel_token: 取消令牌（用于中断执行）。
            force: 忽略已有产物，强制从头执行。

        Returns:
            PipelineResult 包含所有产出路径和元数据。
        """
        return self._run_new_pipeline(
            request, plugin_manager=plugin_manager,
            resume_run_id=resume_run_id, job_queue=job_queue,
            cancel_token=cancel_token, force=force,
        )


# ── V0.6: Collection 辅助 ────────────────────────────────────

def _add_to_collection(
    collection_id: str,
    job_id: str,
    source_uri: str,
    title: str | None,
    notes_path: str,
    template_id: str | None,
    output_dir: str,
) -> None:
    """将成功完成的 job 归入集合（非致命：失败只打印 warning）。"""
    try:
        from src.db.database import connect

        # 产物采用 title/run_<job> 两级目录，不能再从 notes_path 的父级
        # 猜测输出根目录；直接使用管线请求中的 output_dir。
        db_path = get_default_db_path(output_dir)

        if not os.path.exists(db_path):
            logger.warning(f"⚠️  归入集合失败：找不到数据库 {db_path}")
            return

        conn = connect(db_path)
        try:
            from src.application.collections.service import CollectionService
            svc = CollectionService(conn)

            # 确认集合存在
            coll = svc.get_collection(collection_id)
            if coll is None:
                # 集合不存在，自动创建
                coll = svc.create_collection(
                    title=collection_id,
                    collection_type="course",
                )

            svc.add_job(
                collection_id=collection_id,
                job_id=job_id,
                title=title,
                source_uri=source_uri,
                note_path=notes_path,
                status="completed",
                template_id=template_id,
            )
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"⚠️  归入集合失败 ({collection_id}): {e}")
