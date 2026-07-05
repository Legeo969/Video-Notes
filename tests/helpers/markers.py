"""外部依赖标记辅助。

V0.7.2：统一外部依赖检测，避免依赖 ffmpeg/CUDA/OCR/网络 的测试
在默认 CI 运行中失败。
"""

import os
import shutil

import pytest

# ── 环境变量开关 ──────────────────────────────────────────────────
_ENV_EXTERNAL = "RUN_EXTERNAL_TESTS"
_ENV_GPU = "RUN_GPU_TESTS"
_ENV_OCR = "RUN_OCR_TESTS"


def _has_tool(name: str) -> bool:
    """检测命令行工具是否可用。"""
    return shutil.which(name) is not None


def _is_enabled(env_var: str) -> bool:
    """检测环境变量开关是否打开。"""
    return os.getenv(env_var, "").strip() == "1"


# ── pytest marker 工厂 ────────────────────────────────────────────

requires_ffmpeg = pytest.mark.skipif(
    not _has_tool("ffmpeg") or not _is_enabled(_ENV_EXTERNAL),
    reason=f"需要 ffmpeg 且 {_ENV_EXTERNAL}=1",
)

requires_network = pytest.mark.skipif(
    not _is_enabled(_ENV_EXTERNAL),
    reason=f"需要网络访问且 {_ENV_EXTERNAL}=1",
)

requires_cuda = pytest.mark.skipif(
    not _is_enabled(_ENV_GPU),
    reason=f"需要 CUDA/GPU 且 {_ENV_GPU}=1",
)

requires_ocr = pytest.mark.skipif(
    not _is_enabled(_ENV_OCR),
    reason=f"需要 OCR 依赖且 {_ENV_OCR}=1",
)

requires_gui = pytest.mark.skipif(
    not _is_enabled(_ENV_EXTERNAL),
    reason=f"需要 GUI 环境（PySide6/Qt）且 {_ENV_EXTERNAL}=1",
)

# 完整外部环境（需手动开启）
requires_external = pytest.mark.skipif(
    not _is_enabled(_ENV_EXTERNAL),
    reason=f"需要完整外部环境且 {_ENV_EXTERNAL}=1",
)
