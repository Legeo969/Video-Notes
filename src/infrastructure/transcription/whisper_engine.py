"""转录模块 - 使用 faster-whisper"""

import logging
import os
import re
import sys

logger = logging.getLogger(__name__)
_DLL_DIRECTORY_HANDLES = []


def _setup_cuda_env() -> None:
    """自动发现 nvidia pip 包中的 CUDA DLL 并加入 PATH

    ctranslate2 需要 cublas64_12.dll 等运行时库，
    这些 DLL 由 nvidia-cublas-cu12 等 pip 包提供，
    但不在系统 PATH 中。此函数在模块加载时自动设置。
    """
    if sys.platform != 'win32':
        return

    # 优先使用 nvidia pip 包中的 DLL（版本匹配 CUDA 12.x）
    try:
        import site
        sitedirs = [site.getusersitepackages()] + site.getsitepackages()
    except Exception:
        sitedirs = []

    search_roots = list(sitedirs)
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        search_roots.insert(0, frozen_root)
    if getattr(sys, "frozen", False):
        search_roots.insert(0, os.path.dirname(sys.executable))

    # 需要扫描的 nvidia 子包及其 DLL 子目录
    nvidia_subpkgs = [
        ('cublas', 'bin'),
        ('cuda_nvrtc', 'bin'),
        ('cudnn', 'bin'),
        ('cuda_runtime', 'bin'),
    ]

    paths_to_add = []
    for root in search_roots:
        nvidia_base = os.path.join(root, 'nvidia')
        if not os.path.isdir(nvidia_base):
            continue
        for subpkg, dll_subdir in nvidia_subpkgs:
            dll_dir = os.path.join(nvidia_base, subpkg, dll_subdir)
            if os.path.isdir(dll_dir) and dll_dir not in paths_to_add:
                paths_to_add.append(dll_dir)

    if not paths_to_add:
        return

    current_path = os.environ.get('PATH', '')
    new_path = os.pathsep.join(paths_to_add)
    if paths_to_add[0] not in current_path:
        os.environ['PATH'] = new_path + os.pathsep + current_path
        logger.info(f"[CUDA] Added {len(paths_to_add)} DLL directories to PATH")

    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is not None:
        for path in paths_to_add:
            try:
                _DLL_DIRECTORY_HANDLES.append(add_dll_directory(path))
            except OSError:
                logger.debug("[CUDA] Could not add DLL directory: %s", path, exc_info=True)


# 在导入 ctranslate2 之前设置 CUDA 环境
_setup_cuda_env()

import ctranslate2
from faster_whisper import WhisperModel
from functools import lru_cache

# VideoCaptioner 本地模型目录（默认）
DEFAULT_MODEL_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "VideoCaptioner", "AppData", "models",
)
HOME_MODEL_DIR = os.path.join(os.path.expanduser("~"), "faster-whisper")

# 默认转录超参数（可通过环境变量覆盖，方便打包后调整）
_DEFAULT_BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "5"))
_DEFAULT_VAD_FILTER = os.environ.get("WHISPER_VAD_FILTER", "0") == "1"


@lru_cache(maxsize=4)
def _get_cached_model(model_path: str, device: str, compute_type: str) -> WhisperModel:
    """获取或复用已加载的 WhisperModel 实例（模块级缓存，避免重复加载）。

    相同 (model_path, device, compute_type) 组合只加载一次；
    batch 处理多视频时可节省大量模型加载时间。
    """
    return WhisperModel(model_path, device=device, compute_type=compute_type)


def _looks_like_cuda_runtime_error(error: BaseException) -> bool:
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            "cublas",
            "cudnn",
            "cuda",
            "cudart",
            "cannot be loaded",
            "dll",
        )
    )


def _can_fallback_to_cpu(requested_device: str, resolved_device: str, error: BaseException) -> bool:
    return (
        requested_device == "auto"
        and resolved_device == "cuda"
        and _looks_like_cuda_runtime_error(error)
    )


def _load_cpu_fallback_model(model_path: str, error: BaseException) -> WhisperModel:
    logger.warning(
        "CUDA Whisper runtime failed (%s); falling back to CPU/int8. "
        "Explicit CUDA selection will still fail instead of falling back.",
        error,
    )
    _get_cached_model.cache_clear()
    return _get_cached_model(model_path, "cpu", "int8")


def _has_local_models(model_dir: str | None) -> bool:
    if not model_dir or not os.path.isdir(model_dir):
        return False
    for entry in os.listdir(model_dir):
        if entry.startswith("faster-whisper-") and os.path.isdir(
            os.path.join(model_dir, entry)
        ):
            return True
    return False


def _candidate_model_dirs(model_dir: str | None = None) -> list[str]:
    dirs = [
        os.environ.get("WHISPER_MODEL_DIR"),
        model_dir,
        DEFAULT_MODEL_DIR,
        HOME_MODEL_DIR,
    ]
    result = []
    seen = set()
    for d in dirs:
        if not d or d in seen:
            continue
        result.append(d)
        seen.add(d)
    return result


def get_default_model_dir() -> str:
    """返回默认模型目录路径"""
    for model_dir in _candidate_model_dirs():
        if _has_local_models(model_dir):
            return model_dir
    return DEFAULT_MODEL_DIR


def scan_models(model_dir: str) -> list[str]:
    """扫描模型目录，返回所有 faster-whisper-* 文件夹的模型大小名称列表

    例如目录下有 faster-whisper-large-v3/，返回 ["large-v3"]
    """
    if not model_dir or not os.path.isdir(model_dir):
        return []

    models = []
    for entry in sorted(os.listdir(model_dir)):
        if entry.startswith("faster-whisper-") and os.path.isdir(
            os.path.join(model_dir, entry)
        ):
            size = entry[len("faster-whisper-"):]
            models.append(size)
    return models


def _resolve_model(model_size: str, model_dir: str = None) -> str:
    """解析模型路径：只使用本地已有模型，找不到则报错

    Args:
        model_size: 模型大小名称，如 large-v3
        model_dir: 模型目录，为 None 时使用默认目录

    Returns:
        本地模型目录的绝对路径

    Raises:
        FileNotFoundError: 找不到本地模型
    """
    dirs_to_check = _candidate_model_dirs(model_dir)

    for d in dirs_to_check:
        candidates = [
            os.path.join(d, f"faster-whisper-{model_size}"),
            os.path.join(d, model_size),
        ]
        for candidate in candidates:
            if os.path.isdir(candidate):
                return candidate

    searched = " → ".join(dirs_to_check)
    raise FileNotFoundError(
        f"找不到本地 Whisper 模型: faster-whisper-{model_size}\n"
        f"搜索路径: {searched}\n"
        f"请下载模型到上述任意目录，或在 GUI 中指定模型目录。"
    )


def _remove_hallucinations(text: str) -> str:
    """去除 Whisper 转录中的重复段落和幻觉内容"""
    # 只去除 Whisper 已知的幻觉标记，不做泛化匹配以免误删 [C++] 等内容
    text = re.sub(r'\[(MUSIC|APPLAUSE|LAUGHTER|Speech\w*|BLANK_AUDIO)\]', '', text, flags=re.IGNORECASE)
    # 去除纯标点符号的碎片（如只有 ...、——、… 的句子）
    # - 放字符集末尾避免被解析为 range
    text = re.sub(r'[。，、；：!?！？…—–/\.~～\s]{3,}', '', text)

    sentences = text.replace('\n', '。').split('。')
    cleaned = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) < 2:
            continue
        if cleaned and s == cleaned[-1]:
            continue
        cleaned.append(s)
    return '。'.join(cleaned)


def transcribe_with_segments(audio_path: str, model_size: str = "large-v3",
                             language: str = None, model_dir: str = None,
                             beam_size: int | None = None,
                             vad_filter: bool | None = None,
                             compute_type: str | None = None,
                             device: str | None = None):
    """转录音频文件，返回 (全文本, segments列表)

    Args:
        audio_path: 音频文件路径
        model_size: faster-whisper 模型大小 (tiny/base/small/medium/large-v2/large-v3)
        language: 语言代码，None 则自动检测
        model_dir: 模型目录，None 使用默认目录
        beam_size: beam search 宽度，None 使用默认值（环境变量 WHISPER_BEAM_SIZE 或 5）
        vad_filter: 是否启用 VAD 过滤，None 使用默认值（WHISPER_VAD_FILTER）
        compute_type: 计算精度，None/auto 则自动检测（CUDA=float16, CPU=int8）
        device: auto/cuda/cpu。显式选择 cuda 时不可用会直接报错，不静默降级。

    Returns:
        tuple[str, list[dict]]: (full_text, segments)
        每个 segment: {"start": float, "end": float, "text": str}
    """
    model_path = _resolve_model(model_size, model_dir=model_dir)

    requested_device = (device or os.environ.get("WHISPER_DEVICE") or "auto").strip().lower()
    requested_compute = (compute_type or os.environ.get("WHISPER_COMPUTE_TYPE") or "auto").strip().lower()
    if requested_device not in {"auto", "cuda", "cpu"}:
        raise ValueError(f"不支持的 Whisper 运行设备: {requested_device}")

    has_gpu = ctranslate2.get_cuda_device_count() > 0
    explicit_cuda = requested_device == "cuda"
    if explicit_cuda and not has_gpu:
        raise RuntimeError("已选择 Whisper CUDA，但 CTranslate2 未检测到可用 CUDA 设备。")

    resolved_device = "cuda" if (requested_device == "cuda" or (requested_device == "auto" and has_gpu)) else "cpu"
    if requested_compute in {"", "auto", "none"}:
        resolved_compute_type = "float16" if resolved_device == "cuda" else "int8"
    else:
        resolved_compute_type = requested_compute

    try:
        supported_types = ctranslate2.get_supported_compute_types(resolved_device)
    except Exception:
        supported_types = set()
    if supported_types and resolved_compute_type not in supported_types:
        raise RuntimeError(
            f"Whisper {resolved_device} 不支持 compute_type={resolved_compute_type}; "
            f"可用: {sorted(supported_types)}"
        )

    resolved_beam_size = beam_size if beam_size is not None else _DEFAULT_BEAM_SIZE
    resolved_vad_filter = vad_filter if vad_filter is not None else _DEFAULT_VAD_FILTER

    logger.info(
        "📝 正在加载 faster-whisper 模型 (%s) [本地: %s] [device=%s, compute=%s]...",
        model_size, model_path, resolved_device, resolved_compute_type,
    )
    try:
        model = _get_cached_model(model_path, resolved_device, resolved_compute_type)
    except RuntimeError as e:
        if _can_fallback_to_cpu(requested_device, resolved_device, e):
            resolved_device = "cpu"
            resolved_compute_type = "int8"
            model = _load_cpu_fallback_model(model_path, e)
        else:
            raise

    logger.info("📝 正在转录...")
    try:
        segments_gen, info = model.transcribe(
            audio_path,
            language=language,
            beam_size=resolved_beam_size,
            vad_filter=resolved_vad_filter,
        )
    except RuntimeError as e:
        if _can_fallback_to_cpu(requested_device, resolved_device, e):
            resolved_device = "cpu"
            resolved_compute_type = "int8"
            model = _load_cpu_fallback_model(model_path, e)
            segments_gen, info = model.transcribe(
                audio_path,
                language=language,
                beam_size=resolved_beam_size,
                vad_filter=resolved_vad_filter,
            )
        else:
            raise

    # 一次性迭代 segments，分别保存列表和文本
    segments_list: list[dict] = []
    texts: list[str] = []
    try:
        for seg in segments_gen:
            segments_list.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
                "language": info.language or "",  # 供下游 SpeechTranscriber 读取
            })
            texts.append(seg.text)
    except RuntimeError as e:
        if not _can_fallback_to_cpu(requested_device, resolved_device, e):
            raise
        resolved_device = "cpu"
        resolved_compute_type = "int8"
        model = _load_cpu_fallback_model(model_path, e)
        segments_gen, info = model.transcribe(
            audio_path,
            language=language,
            beam_size=resolved_beam_size,
            vad_filter=resolved_vad_filter,
        )
        segments_list = []
        texts = []
        for seg in segments_gen:
            segments_list.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
                "language": info.language or "",
            })
            texts.append(seg.text)

    # 语言感知拼接：中文不加空格，其他语言加空格避免粘词
    detected_lang = info.language or ""
    if detected_lang in ("zh", "ja", "ko"):
        full_text = "".join(t.strip() for t in texts)
    else:
        full_text = " ".join(t.strip() for t in texts if t.strip())

    # 去除幻觉和重复段落（只作用于拼接后的全文，用于 transcript.txt）
    original_len = len(full_text)
    full_text = _remove_hallucinations(full_text)
    removed = original_len - len(full_text)
    if removed > 0:
        logger.info(f"🧹 幻觉去重: 移除 {removed} 字")

    # 对每个 segment 文本做轻量清理（仅移除 [MUSIC] 等标记）
    for seg in segments_list:
        seg["text"] = re.sub(
            r'\[(MUSIC|APPLAUSE|LAUGHTER|Speech\w*|BLANK_AUDIO)\]',
            '', seg["text"], flags=re.IGNORECASE
        ).strip()

    logger.info(f"✅ 转录完成，共 {len(full_text)} 字 (检测语言: {info.language}, device={resolved_device}, compute={resolved_compute_type})")
    return full_text, segments_list


def transcribe(audio_path: str, model_size: str = "large-v3",
               language: str = None, model_dir: str = None) -> str:
    """转录音频文件，返回文本

    内部委托给 transcribe_with_segments，保持向后兼容。

    Args:
        audio_path: 音频文件路径
        model_size: faster-whisper 模型大小 (tiny/base/small/medium/large-v2/large-v3)
        language: 语言代码，None 则自动检测
        model_dir: 模型目录，None 使用默认目录
    """
    text, _ = transcribe_with_segments(audio_path, model_size, language, model_dir)
    return text
