"""System RPC 数据模型"""

from __future__ import annotations

from pydantic import BaseModel


class SystemInfoResponse(BaseModel):
    """引擎系统信息。"""

    shell_version: str = "1.2.0"
    engine_version: str = "1.2.0"
    protocol_version: int = 1
    python_version: str
    cuda_available: bool = False
    ffmpeg_available: bool = False


class SystemCapabilities(BaseModel):
    """引擎能力声明。"""

    max_concurrent_jobs: int = 1
    supports_cuda: bool = False
    supports_ocr: bool = False
