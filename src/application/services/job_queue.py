"""JobQueue — 任务队列管理器 + CancellationToken + ProcessRegistry

PipelineOrchestrator 和 CLI/GUI 之间的桥梁：
- 创建任务 → 更新阶段 → 完成/失败/取消
- 查询任务历史和状态
- 支持断点续跑（基于 StageManifest）
- 支持任务取消（CancellationToken + 进程终止）
- artifact / temp 两层目录分离
"""

from __future__ import annotations

import logging
import os
import signal
import shutil
import sqlite3
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from importlib import import_module
from typing import Callable, Optional

logger = logging.getLogger(__name__)

from src.application.ports.jobs import JobMetadataStore
from src.domain.job_state import (
    JobState,
    JobRecord,
    StageManifest,
    get_stage_order,
    artifact_path,
    temp_path,
)
from src.db.database import compact_database

# Progress callback: preferred signature is
# (run_id, stage, message, percent 0-100). The legacy three-argument callback
# remains supported so older CLI/tests do not break during the migration.
ProgressCallback = Callable[..., None]


# JobQueue instances are created independently by the processing page and the
# task-history page.  Tokens/process handles therefore must be shared per DB,
# otherwise clicking “取消” in the task page only changes SQLite while the real
# worker keeps running.
_SHARED_STATE_LOCK = threading.RLock()
_SHARED_TOKENS: dict[str, dict[int, "CancellationToken"]] = {}
_SHARED_REGISTRIES: dict[str, "ProcessRegistry"] = {}
_SHARED_ACTIVE_RUNS: dict[str, set[int]] = {}


def _create_job_metadata_store(db_path: str) -> JobMetadataStore:
    module = import_module("src.infrastructure.db.processing_metadata")
    return module.ProcessingMetadata(db_path)


def _copy_sqlite_database_snapshot(source: str, destination: str) -> None:
    """Copy a consistent SQLite DB, including committed WAL pages."""
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    tmp_destination = destination + ".tmp"
    try:
        if os.path.exists(tmp_destination):
            os.remove(tmp_destination)
        source_uri = f"file:{os.path.abspath(source)}?mode=ro"
        source_conn = sqlite3.connect(source_uri, uri=True)
        try:
            destination_conn = sqlite3.connect(tmp_destination)
            try:
                source_conn.backup(destination_conn)
                destination_conn.commit()
            finally:
                destination_conn.close()
        finally:
            source_conn.close()
        os.replace(tmp_destination, destination)
    except sqlite3.Error:
        if os.path.exists(tmp_destination):
            os.remove(tmp_destination)
        shutil.copy2(source, destination)


def get_default_app_data_dir() -> str:
    """Return the private per-user data directory used for runtime state."""
    override = os.environ.get("VIDEO_NOTES_DATA_DIR", "").strip()
    if override:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(override)))
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Video Notes AI")
    return os.path.join(os.path.expanduser("~"), ".video-notes-ai")


def get_default_jobs_root() -> str:
    """Return the private checkpoint directory.

    Job workspaces are intentionally not stored in the user output folder.
    Final Markdown/assets belong in the output folder; resumable manifests,
    downloads and temporary audio live under AppData to avoid duplicate user
    visible products and uncontrolled vault growth.
    """
    override = os.environ.get("VIDEO_NOTES_JOBS_DIR", "").strip()
    if override:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(override)))
    return os.path.join(get_default_app_data_dir(), "jobs")


def get_legacy_jobs_root() -> str:
    """Return the pre-state-split hidden jobs directory."""
    return os.path.join(get_default_app_data_dir(), ".jobs")


def get_default_state_dir() -> str:
    """Return the private per-user directory for durable app state."""
    override = os.environ.get("VIDEO_NOTES_STATE_DIR", "").strip()
    if override:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(override)))
    return os.path.join(get_default_app_data_dir(), "state")


# ═══════════════════════════════════════════════════════════════════
# CancellationToken
# ═══════════════════════════════════════════════════════════════════


class CancellationToken:
    """Cooperative stop token with explicit pause/cancel semantics.

    ``pause`` preserves the workspace and manifests for continuation.
    ``cancel`` is destructive: the orchestrator removes the workspace after
    the active stage has stopped.
    """

    def __init__(self):
        self._cancelled = False
        self._action = "pause"
        self._lock = threading.Lock()

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def action(self) -> str:
        with self._lock:
            return self._action

    def request_stop(self, action: str = "pause") -> None:
        if action not in {"pause", "cancel"}:
            raise ValueError(f"unsupported stop action: {action}")
        with self._lock:
            self._action = action
            self._cancelled = True

    def cancel(self, action: str = "pause") -> None:
        """Backward-compatible stop request; defaults to resumable pause."""
        self.request_stop(action)

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            action = self.action
            message = "任务已暂停" if action == "pause" else "任务已取消"
            raise TaskCancelledError(message, action=action)


class TaskCancelledError(Exception):
    """Pipeline stop signal carrying either ``pause`` or ``cancel``."""

    def __init__(self, message: str = "任务已停止", *, action: str = "pause"):
        super().__init__(message)
        self.action = action


# ═══════════════════════════════════════════════════════════════════
# ProcessRegistry
# ═══════════════════════════════════════════════════════════════════


class ProcessRegistry:
    """活跃子进程注册表。

    当 cancel 时，可以终止所有注册的子进程。
    每个进程关联一个 job_id，不同任务互不影响。

    用法：
        registry = ProcessRegistry()
        p = subprocess.Popen(["ffmpeg", ...])
        registry.register("job-123", p)
        # ... later ...
        registry.kill_all("job-123")  # 终止该任务所有子进程
    """

    def __init__(self):
        self._procs: dict[str, list[subprocess.Popen]] = {}
        self._lock = threading.Lock()

    def register(self, job_id: str, proc: subprocess.Popen) -> None:
        """注册一个子进程。"""
        with self._lock:
            self._procs.setdefault(job_id, []).append(proc)

    def unregister(self, job_id: str, proc: subprocess.Popen) -> None:
        """移除一个子进程。"""
        with self._lock:
            procs = self._procs.get(job_id, [])
            if proc in procs:
                procs.remove(proc)

    def kill_all(self, job_id: str) -> int:
        """终止指定任务的所有子进程。返回终止数量。"""
        with self._lock:
            procs = self._procs.pop(job_id, [])
        killed = 0
        for p in procs:
            try:
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait()
                    killed += 1
            except OSError as e:
                logger.warning("进程终止失败 (PID %s): %s", p.pid, e)
        return killed

    def active_count(self, job_id: str | None = None) -> int:
        """返回活跃子进程数。"""
        with self._lock:
            if job_id is not None:
                return sum(1 for p in self._procs.get(job_id, []) if p.poll() is None)
            return sum(
                1 for procs in self._procs.values()
                for p in procs if p.poll() is None
            )


# ═══════════════════════════════════════════════════════════════════
# 原子写入工具
# ═══════════════════════════════════════════════════════════════════


def atomic_write_json(
    filepath: str,
    data: dict | list,
    *,
    min_size: int = 2,
) -> str:
    """原子写入 JSON 文件。

    1. 写入 .tmp 文件
    2. 校验 JSON 可读、最小大小
    3. os.replace 原子替换为正式文件

    返回最终文件路径。
    """
    import json as _json
    tmp = filepath + ".tmp"
    content = _json.dumps(data, ensure_ascii=False, indent=2)
    if len(content.encode("utf-8")) < min_size:
        raise ValueError(f"JSON 内容太小（{len(content)} bytes），拒绝写入 {filepath}")

    # 写入临时文件
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

    # 校验临时文件可读
    with open(tmp, "r", encoding="utf-8") as f:
        _json.load(f)

    # 原子替换
    os.replace(tmp, filepath)
    return filepath


def atomic_write_text(
    filepath: str,
    text: str,
    *,
    min_chars: int = 10,
) -> str:
    """原子写入文本文件。

    1. 写入 .tmp 文件
    2. 校验非空、最小字符数
    3. os.replace 原子替换

    返回最终文件路径。
    """
    if len(text.strip()) < min_chars:
        raise ValueError(f"文本内容太短（{len(text)} 字符），拒绝写入 {filepath}")

    tmp = filepath + ".tmp"
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())

    # 校验可读
    with open(tmp, "r", encoding="utf-8") as f:
        f.read(1)  # 至少能读一个字符

    os.replace(tmp, filepath)
    return filepath


# ═══════════════════════════════════════════════════════════════════
# JobQueue
# ═══════════════════════════════════════════════════════════════════


class JobQueue:
    """任务队列管理器。

    新特性（v0.3.1）：
    - artifact / temp 两层目录
    - 阶段 StageManifest 完整性检查
    - CancellationToken 取消支持
    - ProcessRegistry 进程管理
    - 原子写入中间产物

    用法：
        jq = JobQueue(db_path="output/.note_index/video_notes.db")
        run_id = jq.enqueue("https://...", title="My Video")
        token = jq.create_token()

        try:
            token.raise_if_cancelled()
            jq.update_stage(run_id, JobState.DOWNLOADING)
            # ... 执行下载 ...
            jq.save_stage_manifest(run_id, JobState.DOWNLOADING, manifest)
        except TaskCancelledError:
            jq.cancel(run_id)
    """

    def __init__(
        self,
        db_path: str,
        output_dir: str = "./output",
        on_progress: ProgressCallback | None = None,
        metadata_store: JobMetadataStore | None = None,
    ):
        self.db_path = db_path
        self.output_dir = os.path.abspath(output_dir)
        self.jobs_root = get_default_jobs_root()
        self.meta = metadata_store or _create_job_metadata_store(db_path)
        self._on_progress = on_progress
        self._state_key = os.path.abspath(db_path)
        with _SHARED_STATE_LOCK:
            self._tokens = _SHARED_TOKENS.setdefault(self._state_key, {})
            self._registry = _SHARED_REGISTRIES.setdefault(
                self._state_key, ProcessRegistry()
            )
            self._active_runs = _SHARED_ACTIVE_RUNS.setdefault(self._state_key, set())
        self._lock = _SHARED_STATE_LOCK

    def set_progress_callback(self, callback: ProgressCallback | None) -> None:
        """Attach the engine-level event sink after dependency construction."""
        self._on_progress = callback

    # ── Token 管理 ──

    def create_token(self, run_id: int | None = None) -> CancellationToken:
        """创建一个新的 CancellationToken。

        如果提供 run_id，会自动关联到该任务。
        """
        token = CancellationToken()
        if run_id is not None:
            with self._lock:
                self._tokens[run_id] = token
        return token

    def get_token(self, run_id: int) -> CancellationToken | None:
        """获取任务的 CancellationToken。"""
        with self._lock:
            return self._tokens.get(run_id)

    def mark_worker_active(self, run_id: int) -> None:
        """Mark a run as having a live pipeline worker.

        A token can exist before the background thread actually starts.  Stop
        requests must not treat that placeholder token as proof of a running
        worker, otherwise a pre-start cancel gets stuck forever in
        ``cancelling``.  The active set is shared by all JobQueue instances
        connected to the same database.
        """
        with self._lock:
            self._active_runs.add(run_id)

    def mark_worker_inactive(self, run_id: int) -> None:
        """Remove the live-worker marker after the pipeline exits."""
        with self._lock:
            self._active_runs.discard(run_id)

    def is_worker_active(self, run_id: int) -> bool:
        with self._lock:
            return run_id in self._active_runs

    def finalize_stop(self, run_id: int, action: str) -> bool:
        """Finalize a previously requested stop after the worker has exited."""
        job = self.meta.get_job(run_id)
        if job is None:
            return False
        if action == "pause":
            self.meta.pause_run(run_id)
            self._notify(run_id, JobState.PAUSED, "任务已暂停，断点数据已保留", 0)
            return True
        if action != "cancel":
            raise ValueError(f"unsupported stop action: {action}")
        from src.application.services.cleanup_manager import CleanupManager
        removed = CleanupManager.cleanup_job(job.job_dir, label="已取消任务工作目录")
        if removed:
            self.meta.detach_workspace(run_id)
        self.meta.cancel_run(run_id)
        self._notify(run_id, JobState.CANCELLED, "任务已取消，工作数据已清理", 0)
        return True

    def _request_stop(self, run_id: int, action: str) -> bool:
        job = self.meta.get_job(run_id)
        if job is None or job.is_completed:
            return False
        if job.status in ("paused", "cancelled"):
            return True

        with self._lock:
            token = self._tokens.get(run_id)
        if token is not None:
            token.request_stop(action)

        killed = self._registry.kill_all(job.job_id) if job.job_id else 0
        # A registered token alone does not mean a worker is running: resume
        # creates the token before the QThread starts.  Only the explicit
        # worker marker or a live native child process makes this asynchronous.
        active = self.is_worker_active(run_id) or killed > 0

        if active:
            self.meta.request_stop(run_id, action)
            if action == "pause":
                state, message = JobState.PAUSING, "正在暂停，当前阶段安全退出后保留断点"
            else:
                state, message = JobState.CANCELLING, "正在取消，停止后清理工作数据"
        else:
            # Stale 'running' row after a prior crash: there is no live worker to
            # acknowledge the request, so finalize synchronously.
            self.finalize_stop(run_id, action)
            state = JobState.PAUSED if action == "pause" else JobState.CANCELLED
            message = "任务已暂停，断点数据已保留" if action == "pause" else "任务已取消，工作数据已清理"

        # The running pipeline keeps its own token reference. Removing the lookup
        # entry prevents a new worker from accidentally reusing a cancelled token.
        with self._lock:
            self._tokens.pop(run_id, None)
        if active:
            self._notify(run_id, state, message, 0)
        return True

    def pause_task(self, run_id: int) -> bool:
        """Pause a task and preserve its workspace for continuation."""
        return self._request_stop(run_id, "pause")

    def cancel_task(self, run_id: int) -> bool:
        """Cancel a task and request destructive workspace cleanup."""
        return self._request_stop(run_id, "cancel")

    # ── 进程管理 ──

    def register_process(self, run_id: int, proc: subprocess.Popen) -> None:
        """注册一个子进程到任务。"""
        job = self.meta.get_job(run_id)
        if job and job.job_id:
            self._registry.register(job.job_id, proc)

    def kill_processes(self, run_id: int) -> int:
        """终止任务的所有子进程。"""
        job = self.meta.get_job(run_id)
        if job and job.job_id:
            return self._registry.kill_all(job.job_id)
        return 0

    # ── 生命周期 ──

    def enqueue(
        self,
        input_path: str,
        title: Optional[str] = None,
        job_id: Optional[str] = None,
        request_snapshot: dict | None = None,
        parent_run_id: int | None = None,
        attempt: int = 1,
    ) -> int:
        """创建新任务，返回 run_id。

        创建目录结构：
            .jobs/{job_id}/
            ├── artifacts/
            └── temp/
        """
        job_id = job_id or str(uuid.uuid4())
        job_dir = os.path.join(self.jobs_root, job_id)

        # 创建两层目录
        os.makedirs(os.path.join(job_dir, "artifacts"), exist_ok=True)
        os.makedirs(os.path.join(job_dir, "temp"), exist_ok=True)

        run_id = self.meta.start_run(
            input_path=input_path,
            title=title,
            job_dir=job_dir,
            job_id=job_id,
            request_snapshot=request_snapshot,
            parent_run_id=parent_run_id,
            attempt=attempt,
        )
        self._notify(run_id, JobState.PENDING, f"任务已创建: {title or input_path}", 0)
        return run_id

    def update_stage(
        self,
        run_id: int,
        stage: JobState,
        message: str = "",
        percent: float = 0.0,
    ) -> None:
        """更新任务执行阶段。"""
        self.meta.update_progress(run_id, stage, percent, message)
        self._notify(run_id, stage, message, percent)

    def save_progress(
        self,
        run_id: int,
        stage: JobState,
        percent: float = 0.0,
        message: str = "",
    ) -> None:
        """Update stage progress from pipeline context callbacks."""
        self.update_stage(run_id, stage, message, percent)

    def save_stage_manifest(
        self,
        run_id: int,
        stage: JobState,
        manifest: StageManifest,
    ) -> None:
        """保存阶段完成清单到 job_dir。

        这是原子操作：先写 manifest，再更新 DB stage。
        """
        job = self.meta.get_job(run_id)
        if not job or not job.job_dir:
            return
        manifest.save(job.job_dir)

    def complete(
        self,
        run_id: int,
        notes_path: str = "",
        transcript_path: str = "",
        elapsed_sec: float = 0.0,
        frames_count: int = 0,
        blocks_count: int = 0,
        note_id: Optional[int] = None,
    ) -> None:
        """标记任务完成。"""
        job = self.meta.get_job(run_id)
        job_dir = job.job_dir if job else None
        self.meta.complete_run(
            run_id,
            output_path=notes_path,
            transcript_path=transcript_path,
            elapsed_sec=elapsed_sec,
            frames_count=frames_count,
            blocks_count=blocks_count,
            note_id=note_id,
        )
        # 清理 token
        with self._lock:
            self._tokens.pop(run_id, None)
            self._active_runs.discard(run_id)
        if job_dir and os.path.isdir(job_dir):
            try:
                shutil.rmtree(job_dir)
                self.meta.detach_workspace(run_id)
            except OSError as exc:
                logger.warning("Completed workspace cleanup failed %s: %s", job_dir, exc)
        self._notify(run_id, JobState.COMPLETED, "任务完成", 100)

    def fail(self, run_id: int, error: str) -> None:
        """标记任务失败。"""
        self.meta.fail_run(run_id, error)
        with self._lock:
            self._tokens.pop(run_id, None)
        self._notify(run_id, JobState.FAILED, error, 0)

    def cancel(self, run_id: int) -> None:
        """Backward-compatible alias for a real cancellation request."""
        self.cancel_task(run_id)

    def prepare_resume(self, run_id: int) -> JobRecord:
        """Validate and reset an existing run before background resume."""
        job = self.meta.get_job(run_id)
        if job is None:
            raise ValueError(f"任务不存在: {run_id}")
        if not job.job_dir or not os.path.isdir(job.job_dir):
            raise FileNotFoundError(
                "任务工作目录不存在，无法断点继续；请使用“从头重跑”。"
            )
        self.meta.prepare_resume(run_id)
        token = self.create_token(run_id)
        # Accessing the token here intentionally registers it in shared state.
        _ = token
        refreshed = self.meta.get_job(run_id)
        if refreshed is None:
            raise RuntimeError(f"任务状态重置失败: {run_id}")
        self._notify(run_id, JobState.PENDING, "任务已恢复，正在校验断点", refreshed.progress)
        return refreshed

    def reconcile_interrupted_jobs(self) -> int:
        """Convert stale live states from a previous engine process.

        The engine owns all workers. At process startup there cannot be a live
        worker from the previous process, so any persisted running/stopping row
        is safe to mark as resumable ``interrupted``.
        """
        return self.meta.mark_interrupted_runs(
            "引擎上次运行异常结束；任务工作区已保留，可从断点继续。"
        )

    # ── 断点续跑（基于 manifest） ──

    _PIPELINE_STAGE_GROUPS: dict[JobState, tuple[str, ...]] = {
        JobState.RESOLVING: ("resolve_media",),
        JobState.DOWNLOADING: ("resolve_media",),
        JobState.TRANSCRIBING: ("transcribe",),
        JobState.EXTRACTING_FRAMES: ("extract_frames", "vision_analysis"),
        JobState.GENERATING_NOTES: ("fuse_timeline", "map_notes", "reduce_notes"),
        JobState.INDEXING: ("write_artifacts", "index_provenance"),
    }

    def check_stage_completed(
        self,
        run_id: int,
        stage: JobState,
    ) -> bool:
        """Check the same manifests that the active StageRunner writes."""
        job = self.meta.get_job(run_id)
        if not job or not job.job_dir:
            return False
        from src.application.pipeline.runner import FileManifestStore

        store = FileManifestStore()
        stage_ids = self._PIPELINE_STAGE_GROUPS.get(stage, ())
        if stage_ids and all(
            store.is_completed(job.job_dir, stage_id) for stage_id in stage_ids
        ):
            return True
        # Backward compatibility for V0.3/V11 workspaces.
        legacy = StageManifest.load(job.job_dir, stage.value)
        return bool(legacy and legacy.is_valid(job.job_dir))

    def get_resumable_stage(self, run_id: int) -> JobState | None:
        """Return the first incomplete logical stage for a resumable task."""
        job = self.meta.get_job(run_id)
        if job is None or job.status in {"completed", "cancelled", "cancelling"}:
            return None
        if not job.job_dir or not os.path.isdir(job.job_dir):
            return JobState.RESOLVING
        for stage in (
            JobState.RESOLVING,
            JobState.DOWNLOADING,
            JobState.TRANSCRIBING,
            JobState.EXTRACTING_FRAMES,
            JobState.GENERATING_NOTES,
            JobState.INDEXING,
        ):
            if not self.check_stage_completed(run_id, stage):
                return stage
        return None

    def get_stage_warnings(self, run_id: int) -> list[str]:
        """获取任务的警告信息。

        检查内容：
        - 输出文件是否存在
        - 中间产物完整度
        - manifest 状态
        """
        job = self.meta.get_job(run_id)
        if not job:
            return ["任务不存在"]

        warnings: list[str] = []

        if job.is_completed and job.output_path:
            if not os.path.isfile(job.output_path):
                warnings.append(f"输出笔记文件不存在: {job.output_path}")
            elif os.path.getsize(job.output_path) == 0:
                warnings.append(f"输出笔记文件为空: {job.output_path}")

        if job.is_completed and job.transcript_path:
            if not os.path.isfile(job.transcript_path):
                warnings.append(f"转录文件不存在: {job.transcript_path}")

        if job.job_dir and os.path.isdir(job.job_dir):
            # 检查 manifest 完整性
            order = get_stage_order()
            for stage in order:
                manifest = StageManifest.load(job.job_dir, stage.value)
                if manifest is None:
                    continue
                if manifest.status == "partial":
                    warnings.append(f"阶段 {stage.label} 标记为不完整")
                if not manifest.is_valid(job.job_dir):
                    warnings.append(f"阶段 {stage.label} 产物验证失败")

        return warnings

    # ── 查询 ──

    def get_job(self, run_id: int) -> JobRecord | None:
        """获取任务详情。"""
        return self.meta.get_job(run_id)

    def get_job_by_id(self, job_id: str) -> JobRecord | None:
        """按 UUID 获取任务。"""
        return self.meta.get_job_by_job_id(job_id)

    def list_jobs(
        self,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> list[JobRecord]:
        """列出任务。"""
        return self.meta.list_jobs(limit=limit, offset=offset, status=status)

    def count_jobs(self, status: str | None = None) -> int:
        """统计任务数。"""
        return self.meta.count_jobs(status)

    # ── 产物路径 ──

    def get_job_dir(self, run_id: int) -> str | None:
        """获取任务工作目录。"""
        job = self.meta.get_job(run_id)
        return job.job_dir if job else None

    def get_artifact_path(self, run_id: int, filename: str) -> str | None:
        """获取 artifacts/ 目录下的产物完整路径。"""
        job_dir = self.get_job_dir(run_id)
        if not job_dir:
            return None
        return os.path.join(job_dir, "artifacts", filename)

    def get_temp_path(self, run_id: int, filename: str = "") -> str | None:
        """获取 temp/ 目录下的临时文件路径。"""
        job_dir = self.get_job_dir(run_id)
        if not job_dir:
            return None
        if filename:
            return os.path.join(job_dir, "temp", filename)
        return os.path.join(job_dir, "temp")

    # ── 删除 ──

    def delete_job(
        self,
        run_id: int,
        *,
        delete_workspace: bool = True,
        delete_index: bool = True,
    ) -> bool:
        """Delete task metadata; final exported notes are never removed here."""
        job = self.meta.get_job(run_id)
        if job is None:
            return False
        if job.status in ("running", "pausing", "cancelling"):
            raise RuntimeError("任务仍在运行，请先取消后再删除。")

        if delete_workspace and job.job_dir and os.path.isdir(job.job_dir):
            import shutil
            shutil.rmtree(job.job_dir)
        if delete_index and job.job_id:
            return self.meta.delete_run_with_index(run_id, job.job_id)
        return self.meta.delete_run(run_id)

    def cleanup_orphans(
        self,
        min_age_hours: float = 168.0,
        jobs_root: str | None = None,
    ) -> int:
        """Conservatively remove old, untracked workspaces.

        This method is no longer called by page refresh/startup.  A directory is
        eligible only after ``min_age_hours`` and only when no ``.active`` marker
        exists, avoiding races with a newly created task.
        """
        import shutil
        import time

        jobs_dir = jobs_root or self.jobs_root
        if not os.path.isdir(jobs_dir):
            return 0
        known = {
            os.path.basename(os.path.normpath(j.job_dir))
            for j in self.meta.list_all_jobs(limit=100000)
            if j.job_dir
        }
        cutoff = time.time() - max(0.0, min_age_hours) * 3600.0
        cleaned = 0
        for entry in os.listdir(jobs_dir):
            entry_path = os.path.join(jobs_dir, entry)
            if not os.path.isdir(entry_path) or entry in known:
                continue
            if os.path.exists(os.path.join(entry_path, ".active")):
                continue
            try:
                if os.path.getmtime(entry_path) > cutoff:
                    continue
                shutil.rmtree(entry_path)
                cleaned += 1
            except OSError as exc:
                logger.warning("孤儿工作目录清理失败 %s: %s", entry_path, exc)
        return cleaned

    def cleanup_completed_workspaces(self) -> int:
        """Remove workspaces for completed jobs while preserving history."""
        count = 0
        for job in self.meta.list_all_jobs(limit=100000):
            if job.status != "completed":
                continue
            if job.job_dir and os.path.isdir(job.job_dir):
                shutil.rmtree(job.job_dir)
                count += 1
            self.meta.detach_workspace(job.id)
        return count

    def clear_workspaces(self, *, include_orphans: bool = False) -> int:
        """Remove non-running ``.jobs`` data but keep task history/final outputs."""
        count = 0
        for job in self.meta.list_all_jobs(limit=100000):
            if job.status in ("running", "pausing", "cancelling"):
                continue
            if job.job_dir and os.path.isdir(job.job_dir):
                shutil.rmtree(job.job_dir)
                count += 1
            self.meta.detach_workspace(job.id)
        if include_orphans:
            count += self.cleanup_orphans(min_age_hours=0)
        return count

    def clear_all(self) -> int:
        """Hide task-history rows only.

        The rows remain in SQLite so ``.jobs`` and provenance keep a valid owner;
        the task page filters them out. Workspace cleanup is a separate action.
        """
        active = sum(
            self.meta.count_jobs(status=status)
            for status in ("running", "pausing", "cancelling")
        )
        if active:
            raise RuntimeError("仍有运行或正在停止的任务，不能清空历史。")
        return self.meta.clear_all()

    def purge_hidden_history(self) -> dict[str, object]:
        """Permanently remove hidden task data and compact SQLite.

        Final exported Markdown/transcript/frame bundles are preserved.  Jobs
        referenced by collections are skipped because collection rendering
        still depends on their provenance rows.
        """
        active = [
            job for job in self.meta.list_all_jobs(limit=100000)
            if job.status in ("running", "pausing", "cancelling")
        ]
        if active:
            raise RuntimeError("仍有运行或正在停止的任务，不能永久清理数据库。")

        candidates = self.meta.list_hidden_purge_candidates()
        skipped = self.meta.count_hidden_collection_jobs()

        # Remove resumable workspaces before deleting their database owners.
        # If a filesystem error occurs, keep the DB rows so the user can retry
        # without creating an untracked orphan directory.
        import shutil

        workspace_count = 0
        for item in candidates:
            job_dir = item.get("job_dir")
            if job_dir and os.path.isdir(job_dir):
                shutil.rmtree(job_dir)
                workspace_count += 1

        run_ids = [int(item["id"]) for item in candidates]
        stats = self.meta.purge_hidden_runs(run_ids)
        with self._lock:
            for run_id in run_ids:
                self._tokens.pop(run_id, None)
                self._active_runs.discard(run_id)

        try:
            size_stats = compact_database(self.db_path)
            stats.update(size_stats)
        except Exception as exc:
            # The destructive cleanup has already committed.  Do not report it
            # as wholly failed merely because another short-lived reader kept
            # SQLite from obtaining the exclusive VACUUM lock.
            logger.warning("Database compaction failed after cleanup: %s", exc)
            stats.update(
                {
                    "before_bytes": 0,
                    "after_bytes": 0,
                    "released_bytes": 0,
                    "compaction_error": str(exc),
                }
            )
        stats["workspaces"] = workspace_count
        stats["collection_skipped"] = skipped
        return stats

    # ── 内部 ──

    def _notify(
        self,
        run_id: int,
        stage: JobState,
        message: str,
        percent: float,
    ) -> None:
        if self._on_progress:
            try:
                try:
                    self._on_progress(run_id, stage, message, percent)
                except TypeError:
                    # Temporary compatibility bridge for V13 callbacks.
                    self._on_progress(stage, message, percent)
            except Exception as e:
                logger.warning("Progress callback failed: %s", e)  # 回调不应中断管线


# ── 数据库路径工具 ──


def get_default_db_path(output_dir: str) -> str:
    """获取默认的任务数据库路径。"""
    override = os.environ.get("VIDEO_NOTES_DB_PATH", "").strip()
    if override:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(override)))

    db_path = os.path.join(get_default_state_dir(), "video_notes.db")
    legacy = os.path.join(output_dir, ".note_index", "video_notes.db")
    if not os.path.exists(db_path) and os.path.isfile(legacy):
        _copy_sqlite_database_snapshot(legacy, db_path)
    return db_path


def get_job_dir_from_output(output_dir: str, job_id: str) -> str:
    """Return the private workspace path for ``job_id``.

    ``output_dir`` is ignored for new product builds and kept only for legacy
    callers that still pass it.
    """
    return os.path.join(get_default_jobs_root(), job_id)
