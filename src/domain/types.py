"""Pipeline 数据类型 — PipelineRequest / PipelineResult / JobState

PipelineRequest 接受两种初始化方式：
1. 扁平风格（向后兼容）：
    PipelineRequest(input="...", whisper_model="large-v3", ...)
2. 分组风格（推荐）：
    PipelineRequest(
        input="...",
        transcription=TranscriptionOptions(whisper_model="large-v3"),
        notes=NoteOptions(gpt_model="mimo-v2.5"),
    )

所有扁平字段同时作为只读 property 暴露，确保旧代码 request.whisper_model 仍然可用。
"""

from dataclasses import dataclass, field
from src.domain.job_state import JobState, JobRecord  # noqa: F401 — re-export


# ── 分组选项 ────────────────────────────────────────────────


@dataclass
class TranscriptionOptions:
    """转录参数。"""
    whisper_model: str = "large-v3"  # tiny/base/small/medium/large-v2/large-v3
    model_dir: str | None = None
    language: str | None = None
    beam_size: int = 5
    vad_filter: bool = False
    whisper_device: str = "auto"  # auto | cuda | cpu
    whisper_compute_type: str = "auto"  # auto | float16 | int8_float16 | int8 | float32


@dataclass
class NoteOptions:
    """AI 笔记生成参数。"""
    gpt_model: str = "mimo-v2.5"
    api_key: str | None = None
    base_url: str | None = None
    provider: str | None = None
    template: str | None = None        # 旧：模板文件路径（向后兼容）
    template_id: str | None = None     # 新：YAML 模板 ID（study/meeting/...）
    temperature: float = 0.3
    style: str | None = None
    smart_summary: bool = False
    map_max_workers: int = 6           # MAP 阶段并发 worker 数


@dataclass
class FrameOptions:
    """帧提取参数。"""
    interval: int = 30
    mode: str = "fixed"          # fixed | auto | disabled
    max_frames: int = 30


@dataclass
class VisionOptions:
    """视觉识别 & OCR 参数。"""
    vision_enabled: bool = False
    vision_provider: str | None = None
    vision_model: str | None = None
    vision_api_key: str | None = None
    vision_base_url: str | None = None
    ocr_enabled: bool = False


@dataclass
class OutputOptions:
    """产物输出参数。"""
    output_dir: str = "./output"
    title: str | None = None
    subtitle_format: str = "none"  # srt | ass | txt | none
    vault_path: str | None = None
    bilibili_cookies: str | None = None
    export_mode: str = "clean"  # clean | full — clean: 仅保存笔记引用的帧; full: 保存所有帧
    artifact_layout: str = "versioned"  # versioned | legacy — 默认按 job_id 隔离同名任务产物


# ── 主请求 dataclass ────────────────────────────────────────


@dataclass
class PipelineRequest:
    """一次视频处理任务的完整参数。

    支持两种初始化方式：
    1. 扁平风格（向后兼容）：
        PipelineRequest(input="...", whisper_model="large-v3", output_dir="./out", ...)
    2. 分组风格（推荐）：
        PipelineRequest(
            input="...",
            transcription=TranscriptionOptions(whisper_model="large-v3"),
            notes=NoteOptions(gpt_model="mimo-v2.5"),
        )

    所有旧扁平字段都作为 property 暴露，确保旧代码 request.whisper_model 仍然可用。
    """

    # ── 必填 ──
    input: str  # URL 或本地文件路径

    # ── V0.6: 集合归属 ──
    collection_id: str | None = None  # 所属集合 slug

    # ── 分组选项 ──
    transcription: TranscriptionOptions = field(default_factory=TranscriptionOptions)
    notes: NoteOptions = field(default_factory=NoteOptions)
    frames: FrameOptions = field(default_factory=FrameOptions)
    vision: VisionOptions = field(default_factory=VisionOptions)
    output: OutputOptions = field(default_factory=OutputOptions)
    # ── Flat → nested mapping ──
    _FLAT_MAP = {
        # output
        "output_dir": ("output", "output_dir"),
        "title": ("output", "title"),
        "subtitle_format": ("output", "subtitle_format"),
        "vault_path": ("output", "vault_path"),
        "bilibili_cookies": ("output", "bilibili_cookies"),
        "export_mode": ("output", "export_mode"),
        "artifact_layout": ("output", "artifact_layout"),
        # transcription
        "whisper_model": ("transcription", "whisper_model"),
        "model_dir": ("transcription", "model_dir"),
        "language": ("transcription", "language"),
        "beam_size": ("transcription", "beam_size"),
        "vad_filter": ("transcription", "vad_filter"),
        "whisper_device": ("transcription", "whisper_device"),
        "whisper_compute_type": ("transcription", "whisper_compute_type"),
        # notes
        "gpt_model": ("notes", "gpt_model"),
        "api_key": ("notes", "api_key"),
        "base_url": ("notes", "base_url"),
        "provider": ("notes", "provider"),
        "template": ("notes", "template"),
        "template_id": ("notes", "template_id"),
        "temperature": ("notes", "temperature"),
        "style": ("notes", "style"),
        "smart_summary": ("notes", "smart_summary"),
        "map_max_workers": ("notes", "map_max_workers"),
        # frames
        "frame_interval": ("frames", "interval"),
        "frame_mode": ("frames", "mode"),
        "max_frames": ("frames", "max_frames"),
        # vision
        "vision_enabled": ("vision", "vision_enabled"),
        "vision_provider": ("vision", "vision_provider"),
        "vision_model": ("vision", "vision_model"),
        "vision_api_key": ("vision", "vision_api_key"),
        "vision_base_url": ("vision", "vision_base_url"),
        "ocr_enabled": ("vision", "ocr_enabled"),
    }

    def __init__(self, input: str, **kwargs):
        """接受分组参数或扁平参数（向后兼容）。"""
        # ── V0.6: collection_id ──
        collection_id = kwargs.pop("collection_id", None)

        # 初始化分组对象
        transcription = kwargs.pop("transcription", TranscriptionOptions())
        notes = kwargs.pop("notes", NoteOptions())
        frames = kwargs.pop("frames", FrameOptions())
        vision = kwargs.pop("vision", VisionOptions())
        output = kwargs.pop("output", OutputOptions())

        # → 扁平参数映射到分组字段
        for flat_key, (group_name, field_name) in self._FLAT_MAP.items():
            if flat_key in kwargs:
                group_obj = locals()[group_name]
                setattr(group_obj, field_name, kwargs.pop(flat_key))

        # 忽略未知参数（不同 API 版本可能多传字段）
        if kwargs:
            for k in kwargs:
                pass  # silently ignored

        # write back via object.__setattr__（dataclass __init__ 之后）
        object.__setattr__(self, "input", input)
        object.__setattr__(self, "collection_id", collection_id)
        object.__setattr__(self, "transcription", transcription)
        object.__setattr__(self, "notes", notes)
        object.__setattr__(self, "frames", frames)
        object.__setattr__(self, "vision", vision)
        object.__setattr__(self, "output", output)
        object.__setattr__(self, "use_new_pipeline", kwargs.pop("use_new_pipeline", True))
        object.__setattr__(self, "use_legacy_pipeline", kwargs.pop("use_legacy_pipeline", False))

    # ── 向后兼容 flat property ──

    @property
    def output_dir(self) -> str: return self.output.output_dir
    @output_dir.setter
    def output_dir(self, v: str): self.output.output_dir = v

    @property
    def title(self) -> str | None: return self.output.title
    @title.setter
    def title(self, v: str | None): self.output.title = v

    @property
    def language(self) -> str | None: return self.transcription.language
    @language.setter
    def language(self, v: str | None): self.transcription.language = v

    @property
    def whisper_model(self) -> str: return self.transcription.whisper_model
    @whisper_model.setter
    def whisper_model(self, v: str): self.transcription.whisper_model = v

    @property
    def model_dir(self) -> str | None: return self.transcription.model_dir
    @model_dir.setter
    def model_dir(self, v: str | None): self.transcription.model_dir = v

    @property
    def beam_size(self) -> int: return self.transcription.beam_size
    @beam_size.setter
    def beam_size(self, v: int): self.transcription.beam_size = v

    @property
    def vad_filter(self) -> bool: return self.transcription.vad_filter
    @vad_filter.setter
    def vad_filter(self, v: bool): self.transcription.vad_filter = v

    @property
    def whisper_device(self) -> str: return self.transcription.whisper_device
    @whisper_device.setter
    def whisper_device(self, v: str): self.transcription.whisper_device = v

    @property
    def whisper_compute_type(self) -> str: return self.transcription.whisper_compute_type
    @whisper_compute_type.setter
    def whisper_compute_type(self, v: str): self.transcription.whisper_compute_type = v

    @property
    def gpt_model(self) -> str: return self.notes.gpt_model
    @gpt_model.setter
    def gpt_model(self, v: str): self.notes.gpt_model = v

    @property
    def api_key(self) -> str | None: return self.notes.api_key
    @api_key.setter
    def api_key(self, v: str | None): self.notes.api_key = v

    @property
    def base_url(self) -> str | None: return self.notes.base_url
    @base_url.setter
    def base_url(self, v: str | None): self.notes.base_url = v

    @property
    def provider(self) -> str | None: return self.notes.provider
    @provider.setter
    def provider(self, v: str | None): self.notes.provider = v

    @property
    def template(self) -> str | None: return self.notes.template
    @template.setter
    def template(self, v: str | None): self.notes.template = v

    @property
    def template_id(self) -> str | None: return self.notes.template_id
    @template_id.setter
    def template_id(self, v: str | None): self.notes.template_id = v

    @property
    def temperature(self) -> float: return self.notes.temperature
    @temperature.setter
    def temperature(self, v: float): self.notes.temperature = v

    @property
    def style(self) -> str | None: return self.notes.style
    @style.setter
    def style(self, v: str | None): self.notes.style = v

    @property
    def smart_summary(self) -> bool: return self.notes.smart_summary
    @smart_summary.setter
    def smart_summary(self, v: bool): self.notes.smart_summary = v

    @property
    def map_max_workers(self) -> int: return self.notes.map_max_workers
    @map_max_workers.setter
    def map_max_workers(self, v: int): self.notes.map_max_workers = v

    @property
    def frame_interval(self) -> int: return self.frames.interval
    @frame_interval.setter
    def frame_interval(self, v: int): self.frames.interval = v

    @property
    def frame_mode(self) -> str: return self.frames.mode
    @frame_mode.setter
    def frame_mode(self, v: str): self.frames.mode = v

    @property
    def max_frames(self) -> int: return self.frames.max_frames
    @max_frames.setter
    def max_frames(self, v: int): self.frames.max_frames = v

    @property
    def vision_enabled(self) -> bool: return self.vision.vision_enabled
    @vision_enabled.setter
    def vision_enabled(self, v: bool): self.vision.vision_enabled = v

    @property
    def vision_provider(self) -> str | None: return self.vision.vision_provider
    @vision_provider.setter
    def vision_provider(self, v: str | None): self.vision.vision_provider = v

    @property
    def vision_model(self) -> str | None: return self.vision.vision_model
    @vision_model.setter
    def vision_model(self, v: str | None): self.vision.vision_model = v

    @property
    def vision_api_key(self) -> str | None: return self.vision.vision_api_key
    @vision_api_key.setter
    def vision_api_key(self, v: str | None): self.vision.vision_api_key = v

    @property
    def vision_base_url(self) -> str | None: return self.vision.vision_base_url
    @vision_base_url.setter
    def vision_base_url(self, v: str | None): self.vision.vision_base_url = v

    @property
    def ocr_enabled(self) -> bool: return self.vision.ocr_enabled
    @ocr_enabled.setter
    def ocr_enabled(self, v: bool): self.vision.ocr_enabled = v

    @property
    def artifact_layout(self) -> str: return self.output.artifact_layout
    @artifact_layout.setter
    def artifact_layout(self, v: str): self.output.artifact_layout = v

    @property
    def subtitle_format(self) -> str: return self.output.subtitle_format
    @subtitle_format.setter
    def subtitle_format(self, v: str): self.output.subtitle_format = v

    @property
    def vault_path(self) -> str | None: return self.output.vault_path
    @vault_path.setter
    def vault_path(self, v: str | None): self.output.vault_path = v

    @property
    def bilibili_cookies(self) -> str | None: return self.output.bilibili_cookies
    @bilibili_cookies.setter
    def bilibili_cookies(self, v: str | None): self.output.bilibili_cookies = v

    @property
    def export_mode(self) -> str: return self.output.export_mode
    @export_mode.setter
    def export_mode(self, v: str): self.output.export_mode = v

    # ── ProviderConfig 工厂方法 ──

    def main_llm_config(self):
        from src.application.providers.config import ProviderConfig
        return ProviderConfig(
            provider=self.provider or "mimo",
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.gpt_model,
        )

    def vision_llm_config(self):
        from src.application.providers.config import ProviderConfig
        if not self.vision_provider:
            return None
        return ProviderConfig(
            provider=self.vision_provider,
            api_key=self.vision_api_key,
            base_url=self.vision_base_url,
            model=self.vision_model,
        )


# ── 结果 dataclass ───────────────────────────────────────────


@dataclass
class PipelineResult:
    """一次处理完成后的产出信息。"""

    notes_path: str          # 生成的 Markdown 笔记绝对路径
    transcript_path: str     # 转录文本绝对路径
    title: str               # 最终确定的标题
    input: str               # 原始输入
    elapsed_sec: float = 0   # 总耗时（秒）
    frames_count: int = 0    # 提取的帧数
    note_id: int | None = None  # 数据库 notes 表主键
    job_id: str | None = None   # V0.4: 任务 ID，用于 provenance 追踪


# RuntimeCapabilities moved to src/utils/runtime.py
# Re-exported from src/core/services/types.py
