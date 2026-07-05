"""V0.5 模板系统数据模型。

定义 NoteTemplate、TemplateSection、TemplateValidationResult 等核心 dataclass。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── 异常 ───────────────────────────────────────────────────────


class TemplateError(Exception):
    """模板加载或校验错误。"""

    def __init__(self, template_id: str, message: str) -> None:
        super().__init__(f"模板 [{template_id}]: {message}")
        self.template_id = template_id
        self.message = message


# ── 模板数据模型 ──────────────────────────────────────────────


@dataclass
class TemplateSection:
    """模板中的一个章节定义。"""

    id: str
    title: str
    required: bool = True
    description: str | None = None


@dataclass
class TemplateOutputOptions:
    """模板输出配置。

    注意：with_citations 优先级为 CLI 显式传入 > template 配置 > 默认 false。
    """

    format: str = "markdown"
    with_timestamps: bool = True
    with_citations: bool = False
    language: str | None = None


@dataclass
class NoteTemplate:
    """一个完整的笔记模板。

    Attributes:
        id: 唯一标识符，如 "study"。
        name: 显示名称，如 "学习笔记"。
        description: 模板说明。
        version: 模板版本号。
        sections: 章节定义列表。
        prompt: 模板的角色/要求 prompt（会被注入到最终的 LLM prompt 中）。
        output: 输出配置选项。
    """

    id: str
    name: str
    description: str
    version: int
    sections: list[TemplateSection]
    prompt: str
    output: TemplateOutputOptions = field(default_factory=TemplateOutputOptions)


# ── 上下文 ─────────────────────────────────────────────────────


@dataclass
class NoteContext:
    """笔记生成上下文。"""

    title: str | None = None
    source_url: str | None = None
    language: str | None = None
    duration: float | None = None
    with_citations: bool = False


# ── 校验 ───────────────────────────────────────────────────────


@dataclass
class TemplateValidationWarning:
    """单个校验告警。"""

    level: str  # "error" | "warning"
    section_id: str | None
    message: str


@dataclass
class TemplateValidationResult:
    """模板校验结果。"""

    passed: bool = True
    warnings: list[TemplateValidationWarning] = field(default_factory=list)

    @property
    def errors(self) -> list[TemplateValidationWarning]:
        return [w for w in self.warnings if w.level == "error"]

    @property
    def infos(self) -> list[TemplateValidationWarning]:
        return [w for w in self.warnings if w.level == "warning"]

    def add_warning(self, section_id: str | None, message: str) -> None:
        self.warnings.append(
            TemplateValidationWarning(level="warning", section_id=section_id, message=message)
        )

    def add_error(self, section_id: str | None, message: str) -> None:
        self.warnings.append(
            TemplateValidationWarning(level="error", section_id=section_id, message=message)
        )
        self.passed = False

    def to_text(self) -> str:
        """生成人类可读的校验报告。"""
        if not self.warnings:
            return "✓ 校验通过，未发现问题。"

        lines = ["模板校验报告："]
        for w in self.warnings:
            icon = "❌" if w.level == "error" else "⚠️"
            section_str = f"[{w.section_id}] " if w.section_id else ""
            lines.append(f"  {icon} {section_str}{w.message}")
        return "\n".join(lines)
