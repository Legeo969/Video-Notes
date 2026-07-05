"""转录后端抽象层。

定义统一的 TranscriptionBackend Protocol 和 Transcript 数据类，
使上层代码无需感知具体后端（faster-whisper / whisper.cpp / API）。

用法示例：
    from src.infrastructure.transcription.backends import get_backend, BackendType

    backend = get_backend("faster_whisper", model_size="large-v3")
    result = backend.transcribe("audio.wav")
    print(result.text)
    for seg in result.segments:
        print(seg["start"], seg["text"])
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class Transcript:
    """转录结果容器。

    Attributes:
        text: 全文拼接字符串（语言感知 join）。
        segments: 段落列表，每项为 {"start": float, "end": float, "text": str}。
        language: 检测到的语言代码（如 "zh"、"en"）。
        backend: 生成此结果的后端名称（如 "faster_whisper"）。
        model: 使用的模型标识符（如 "large-v3"）。
    """

    text: str
    segments: list[dict] = field(default_factory=list)
    language: str = ""
    backend: str = ""
    model: str = ""


@runtime_checkable
class TranscriptionBackend(Protocol):
    """转录后端协议。

    所有具体后端实现此协议即可被上层使用，无需继承。

    Attributes:
        name: 后端唯一名称，用于日志和设置存储（如 "faster_whisper"）。
    """

    name: str

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        **kwargs,
    ) -> Transcript:
        """转录音频文件。

        Args:
            audio_path: 音频文件的本地路径。
            language: 语言代码（如 "zh"、"en"），None 则自动检测。
            **kwargs: 后端特定的额外参数（beam_size、vad_filter 等）。

        Returns:
            Transcript 对象，含全文和段落列表。

        Raises:
            FileNotFoundError: 音频文件不存在。
            RuntimeError: 后端初始化或推理失败。
        """
        ...

    def is_available(self) -> bool:
        """检查后端所需依赖是否已安装可用。

        Returns:
            True 表示可用，False 表示缺少依赖或未初始化。
        """
        ...


# ---------------------------------------------------------------------------
# Registry & factory
# ---------------------------------------------------------------------------

BackendType = str  # "faster_whisper" | "whisper_cpp" | "cloud"

_REGISTRY: dict[str, type] = {}


def register_backend(name: str):
    """类装饰器，将后端类注册到全局注册表。

    Usage::
        @register_backend("my_backend")
        class MyBackend:
            ...
    """
    def decorator(cls):
        _REGISTRY[name] = cls
        return cls
    return decorator


def get_backend(
    backend_type: BackendType = "faster_whisper",
    **kwargs,
) -> TranscriptionBackend:
    """工厂函数：按名称创建并返回后端实例。

    Args:
        backend_type: 后端标识符，支持 "faster_whisper" / "whisper_cpp"。
        **kwargs: 传递给后端构造函数的参数（如 model_size、model_dir）。

    Returns:
        实现了 TranscriptionBackend 协议的对象。

    Raises:
        ValueError: 未知的 backend_type。
        ImportError: 后端依赖未安装。
    """
    # 延迟导入以避免未安装依赖时崩溃
    if backend_type not in _REGISTRY:
        _ensure_builtin_loaded()

    if backend_type not in _REGISTRY:
        available = list(_REGISTRY.keys())
        raise ValueError(
            f"未知转录后端: {backend_type!r}。"
            f"可用后端: {available}。"
            f"请确认相关依赖已安装。"
        )

    cls = _REGISTRY[backend_type]
    return cls(**kwargs)


def list_backends() -> list[str]:
    """返回所有已注册的后端名称列表。"""
    _ensure_builtin_loaded()
    return list(_REGISTRY.keys())


def _ensure_builtin_loaded() -> None:
    """确保内置后端模块已被导入（触发 @register_backend 装饰器）。"""
    # 导入即注册，忽略 ImportError（依赖未安装的后端跳过注册）
    for module_name in (
        "src.infrastructure.transcription.faster_whisper_backend",
        "src.infrastructure.transcription.whisper_cpp_backend",
    ):
        try:
            __import__(module_name)
        except ImportError:
            pass