"""pytest 全局配置。

V0.7.2：添加外部依赖命令行开关，支持分层测试运行策略。
"""

import sys
from unittest.mock import MagicMock

import pytest

# ── 只 mock 确实缺失的第三方模块 ──
_MISSING_MODULES = [
    "PIL",
    "openai",
    "PySide6",
    "ctranslate2",
    "faster_whisper",
    "scenedetect",
    "cv2",
    "paddle",
    "paddleocr",
    "whispercpp",
    "yt_dlp",
    "opensearchpy",
    "pypdf",
    "markdown",
    "yaml",
    "bs4",
    "lxml",
    "docx",
    "tiktoken",
]
def _ensure_optional_module_mock(mod_name: str) -> None:
    """Import *mod_name*; mock it with MagicMock on any failure (ImportError, OSError, etc.)."""
    try:
        __import__(mod_name)
    except Exception:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()


for _mod_name in _MISSING_MODULES:
    _ensure_optional_module_mock(_mod_name)


def pytest_addoption(parser):
    """自定义命令行选项。"""
    parser.addoption(
        "--run-external",
        action="store_true",
        default=False,
        help="启用需要外部二进制/模型/网络/可选包的测试",
    )
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="启用长时间运行测试",
    )
    parser.addoption(
        "--run-gpu",
        action="store_true",
        default=False,
        help="启用需要 CUDA/GPU 的测试",
    )
    parser.addoption(
        "--run-ocr",
        action="store_true",
        default=False,
        help="启用需要 OCR 依赖的测试",
    )
