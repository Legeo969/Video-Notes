"""任务状态机 — JobState 枚举 + JobRecord / StageManifest 数据模型

用法：
    state = JobState.PENDING
    state == JobState.TRANSCRIBING  # True
    json.dumps(state)               # "pending"

    record = JobRecord(id=1, job_id="abc123", input="...", ...)

    manifest = StageManifest(
        stage="transcribing",
        status="completed",
        outputs=["transcript.json"],
        input_hash="abc123...",
    )
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from enum import Enum


class JobState(str, Enum):
    """视频处理管线的分阶段状态。

    每个阶段对应 PipelineOrchestrator 中的一步操作。
    终端状态：COMPLETED / FAILED / CANCELLED。
    """

    PENDING = "pending"
    RESOLVING = "resolving"             # 解析输入源（URL/本地文件）
    DOWNLOADING = "downloading"         # yt-dlp 下载
    TRANSCRIBING = "transcribing"       # Whisper 转录
    EXTRACTING_FRAMES = "extracting_frames"   # 抽帧
    GENERATING_NOTES = "generating_notes"     # LLM 笔记生成
    INDEXING = "indexing"               # 知识库写入
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSING = "pausing"
    CANCELLING = "cancelling"
    PAUSED = "paused"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """是否终端状态（不会再流转）。"""
        return self in (JobState.COMPLETED, JobState.FAILED, JobState.PAUSED, JobState.CANCELLED)

    @property
    def is_running(self) -> bool:
        """是否正在执行中（非终端、非等待）。"""
        return self not in (
            JobState.PENDING,
            JobState.COMPLETED,
            JobState.FAILED,
            JobState.PAUSED,
            JobState.CANCELLED,
        )

    @property
    def label(self) -> str:
        """人类可读的中文标签。"""
        _LABELS = {
            JobState.PENDING: "等待中",
            JobState.RESOLVING: "解析输入源",
            JobState.DOWNLOADING: "下载中",
            JobState.TRANSCRIBING: "转录中",
            JobState.EXTRACTING_FRAMES: "抽帧中",
            JobState.GENERATING_NOTES: "AI 生成笔记中",
            JobState.INDEXING: "写入知识库",
            JobState.COMPLETED: "已完成",
            JobState.FAILED: "失败",
            JobState.PAUSING: "正在暂停",
            JobState.CANCELLING: "正在取消",
            JobState.PAUSED: "已暂停",
            JobState.CANCELLED: "已取消",
        }
        return _LABELS.get(self, self.value)


# ── 目录结构 ──
#
# .jobs/{job_id}/
# ├── artifacts/        # 可追溯产物，成功后保留
# │   ├── transcript.json
# │   ├── notes.md
# │   ├── frames/       # 抽帧截图
# │   └── blocks.json
# ├── temp/             # 可删除临时文件，成功后清理
# │   ├── audio.wav
# │   ├── download/     # yt-dlp 下载缓存
# │   └── frames_work/  # ffmpeg 临时帧
# └── job_state.json    # 总体任务状态
# ──────────────────────────────────────────────

# 每个阶段完成后在 artifacts/ 中生成的标记文件
_STAGE_ARTIFACTS: dict[JobState, str] = {
    JobState.RESOLVING: "artifacts/audio.wav",
    JobState.DOWNLOADING: "artifacts/download.done",
    JobState.TRANSCRIBING: "artifacts/transcript.json",
    JobState.EXTRACTING_FRAMES: "artifacts/frames.done",
    JobState.GENERATING_NOTES: "artifacts/notes.md",
    JobState.INDEXING: "artifacts/index.done",
}

# 阶段输出产物列表（写入 artifacts/ 目录）
_STAGE_OUTPUTS: dict[JobState, list[str]] = {
    JobState.RESOLVING: ["audio.wav"],
    JobState.DOWNLOADING: ["download.done"],
    JobState.TRANSCRIBING: ["transcript.json"],
    JobState.EXTRACTING_FRAMES: ["frames.done"],
    JobState.GENERATING_NOTES: ["notes.md"],
    JobState.INDEXING: ["index.done"],
}

# 阶段执行顺序（用于判断哪些阶段已完成）
_STAGE_ORDER: list[JobState] = [
    JobState.RESOLVING,
    JobState.DOWNLOADING,
    JobState.TRANSCRIBING,
    JobState.EXTRACTING_FRAMES,
    JobState.GENERATING_NOTES,
    JobState.INDEXING,
]


def get_stage_artifact(stage: JobState) -> str | None:
    """返回阶段完成时产生的标记文件名（相对于 job_dir）。"""
    return _STAGE_ARTIFACTS.get(stage)


def get_stage_outputs(stage: JobState) -> list[str]:
    """返回阶段产生的所有产物文件名列表。"""
    return list(_STAGE_OUTPUTS.get(stage, []))


def get_stage_order() -> list[JobState]:
    """返回阶段执行顺序。"""
    return list(_STAGE_ORDER)


def get_next_stage(current: JobState) -> JobState | None:
    """返回当前阶段的下一阶段，如果已是最后阶段返回 None。"""
    try:
        idx = _STAGE_ORDER.index(current)
        return _STAGE_ORDER[idx + 1] if idx + 1 < len(_STAGE_ORDER) else None
    except ValueError:
        return None


def artifact_path(job_dir: str, filename: str) -> str:
    """获取 artifact 文件的完整路径。"""
    return os.path.join(job_dir, "artifacts", filename)


def temp_path(job_dir: str, filename: str) -> str:
    """获取 temp 文件的完整路径。"""
    return os.path.join(job_dir, "temp", filename)


# ── 阶段 Manifest ──


@dataclass
class StageManifest:
    """单个阶段的完整性标记。

    每次阶段完成时写入 artifacts/_manifest_{stage}.json，
    用于断点续跑时判断阶段是否真正完成（而非仅靠文件存在）。
    """

    stage: str                          # JobState 值
    status: str                         # "completed" | "partial"
    outputs: list[str] = field(default_factory=list)  # 产物文件名列表
    input_hash: str = ""                # 输入内容的 SHA-256
    version: int = 1                    # manifest 格式版本
    created_at: str = ""                # ISO 时间戳
    error: str = ""                     # partial 时的错误信息

    def is_valid(self, job_dir: str) -> bool:
        """校验 manifest 指向的所有产物文件存在且非空。"""
        if self.status != "completed":
            return False
        if not self.outputs:
            return False
        for output_file in self.outputs:
            full_path = os.path.join(job_dir, "artifacts", output_file)
            if not os.path.isfile(full_path):
                return False
            if os.path.getsize(full_path) == 0:
                return False
        return True

    @staticmethod
    def compute_hash(data: str | bytes) -> str:
        """计算输入内容的 SHA-256。"""
        if isinstance(data, str):
            data = data.encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def load(job_dir: str, stage: str) -> StageManifest | None:
        """从磁盘加载阶段 manifest。

        优先读取新路径 {job_dir}/artifacts/_manifest/_manifest_{stage}.json，
        若不存在则回退到旧路径 {job_dir}/_manifest_{stage}.json。
        """
        # 尝试新路径
        path = os.path.join(job_dir, "artifacts", "_manifest", f"_manifest_{stage}.json")
        if not os.path.isfile(path):
            # 回退到旧路径
            path = os.path.join(job_dir, f"_manifest_{stage}.json")
            if not os.path.isfile(path):
                return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return StageManifest(
                stage=raw.get("stage", stage),
                status=raw.get("status", "partial"),
                outputs=raw.get("outputs", []),
                input_hash=raw.get("input_hash", ""),
                version=raw.get("version", 1),
                created_at=raw.get("created_at", ""),
                error=raw.get("error", ""),
            )
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def save(self, job_dir: str) -> str:
        """保存 manifest 到 {job_dir}/artifacts/_manifest/_manifest_{stage}.json。"""
        manifest_dir = os.path.join(job_dir, "artifacts", "_manifest")
        os.makedirs(manifest_dir, exist_ok=True)
        path = os.path.join(manifest_dir, f"_manifest_{self.stage}.json")
        data = {
            "stage": self.stage,
            "status": self.status,
            "outputs": self.outputs,
            "input_hash": self.input_hash,
            "version": self.version,
            "created_at": self.created_at,
            "error": self.error,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path


# ── 数据模型 ──


@dataclass
class JobRecord:
    """一条完整的任务记录，对应 processing_runs 表的一行。"""

    id: int                              # 数据库主键
    job_id: str                          # UUID，用于文件命名
    input: str                           # 原始输入（URL 或本地路径）
    title: str | None = None             # 视频标题
    status: str = "pending"              # 整体状态
    stage: str = "pending"               # 当前执行阶段
    output_path: str | None = None       # 最终笔记路径
    transcript_path: str | None = None   # 转录文本路径
    error_message: str | None = None     # 失败原因
    job_dir: str | None = None           # 工作目录 (.jobs/{job_id}/)
    started_at: str | None = None        # 开始时间
    completed_at: str | None = None      # 结束时间
    elapsed_sec: float = 0.0             # 总耗时（秒）
    frames_count: int = 0                # 抽帧数
    blocks_count: int = 0                # 知识块数
    note_id: int | None = None           # notes 表主键

    @property
    def state(self) -> JobState:
        """当前阶段作为 JobState 枚举。"""
        try:
            return JobState(self.stage)
        except ValueError:
            return JobState.PENDING

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"

    @property
    def is_paused(self) -> bool:
        return self.status == "paused"

    @property
    def is_cancelled(self) -> bool:
        return self.status == "cancelled"

    @property
    def is_running(self) -> bool:
        return self.status in ("running", "pausing", "cancelling")

    @property
    def can_resume(self) -> bool:
        """任务是否可以断点续跑。

        completed → 无需续跑（返回 False）
        running → 可能已崩，可以续跑
        failed/paused → 可以续跑
        cancelled → 工作区已清理，只能新建任务从头重跑
        pending → 尚未执行，从头开始
        """
        if self.status in ("failed", "paused", "running", "pending"):
            return True
        return False
