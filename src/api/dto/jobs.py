"""Job/Process RPC 数据模型

这些 DTO 不直接暴露内部 dataclass（JobRecord），
而是提供稳定的 API 合约层。
"""

from __future__ import annotations

from pydantic import BaseModel
from typing import Any


class JobInfo(BaseModel):
    """任务摘要信息。"""

    id: int                     # 数据库主键 (run_id)
    job_id: str                 # UUID
    title: str | None = None
    input: str
    status: str = "pending"     # pending / running / paused / interrupted / completed / failed / cancelled
    stage: str = "pending"      # 当前执行阶段
    progress: float = 0.0       # 0-100
    created_at: str | None = None  # ISO 8601
    completed_at: str | None = None
    elapsed_sec: float = 0.0
    error_message: str | None = None
    output_path: str | None = None
    transcript_path: str | None = None
    frames_count: int = 0
    note_id: int | None = None
    progress_message: str | None = None
    last_active_stage: str | None = None
    attempt: int = 1
    parent_run_id: int | None = None
    can_resume: bool = False


class JobStartRequest(BaseModel):
    """启动新任务的请求参数。"""

    input: str                                               # URL 或本地路径
    whisper_model: str = "large-v3"
    title: str | None = None
    language: str | None = None
    ocr_enabled: bool = False
    vision_enabled: bool = False
    vision_provider: str | None = None
    vision_model: str | None = None
    model_dir: str | None = None
    beam_size: int = 5
    vad_filter: bool = False
    gpt_model: str = "mimo-v2.5"
    temperature: float = 0.3
    style: str | None = None
    template: str | None = None
    template_id: str | None = None
    output_dir: str = "./output"
    subtitle_format: str = "none"
    collection_id: str | None = None
    frame_interval: int = 30
    frame_mode: str = "fixed"
    max_frames: int = 30
    smart_summary: bool = False
    map_max_workers: int = 6
    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None


class JobStartResponse(BaseModel):
    """启动任务响应。"""

    job_id: int   # 数据库主键 (run_id)


class JobListParams(BaseModel):
    """任务列表查询参数。"""

    limit: int = 20
    offset: int = 0
    status: str | None = None
