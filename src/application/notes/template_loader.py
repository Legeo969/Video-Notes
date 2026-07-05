"""V0.5 TemplateRegistry — 模板加载、注册、发现。

从 YAML 文件加载 NoteTemplate，支持内置模板和用户自定义模板。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from src.domain.models.note_template import (  # noqa: E402
    NoteTemplate,
    TemplateError,
    TemplateOutputOptions,
    TemplateSection,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ── 内置模板目录 ──────────────────────────────────────────────
# 文件: src/application/notes/template_loader.py
# 模板: src/core/notes/templates/
# Path(__file__).resolve().parents[2] = src/

_BUILTIN_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[2] / "core" / "notes" / "templates"
)

# YAML 必需字段
_REQUIRED_FIELDS = {"id", "name", "description", "version", "prompt"}

# 模板 ID 校验：只允许字母、数字、下划线、连字符
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


# ── YAML 解析 ─────────────────────────────────────────────────


def _validate_template_id(template_id: str) -> None:
    """校验模板 ID 格式。"""
    if not _ID_RE.match(template_id):
        raise TemplateError(
            template_id,
            f"模板 ID 格式无效：'{template_id}'。必须以小写字母开头，仅包含 a-z、0-9、_、-",
        )


def _parse_template_yaml(data: dict) -> NoteTemplate:
    """从 YAML 数据构造 NoteTemplate。

    Raises:
        TemplateError: 缺少必需字段或格式错误。
    """
    tid = data.get("id", "?")
    if isinstance(tid, str):
        _validate_template_id(tid)
    else:
        raise TemplateError(str(tid), "模板 ID 必须为字符串")

    # 检查必需字段
    missing = _REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise TemplateError(tid, f"缺少必需字段：{', '.join(sorted(missing))}")

    # 解析 sections
    sections: list[TemplateSection] = []
    for i, s in enumerate(data.get("sections", [])):
        if not isinstance(s, dict):
            raise TemplateError(tid, f"sections[{i}] 不是有效的字典")
        sid = s.get("id", f"section_{i}")
        sections.append(
            TemplateSection(
                id=str(sid),
                title=s.get("title", str(sid)),
                required=s.get("required", True),
                description=s.get("description"),
            )
        )

    # 解析 output
    output_data = data.get("output", {})
    if not isinstance(output_data, dict):
        output_data = {}
    output = TemplateOutputOptions(
        format=output_data.get("format", "markdown"),
        with_timestamps=output_data.get("with_timestamps", True),
        with_citations=output_data.get("with_citations", False),
        language=output_data.get("language"),
    )

    return NoteTemplate(
        id=str(data["id"]),
        name=str(data.get("name", data["id"])),
        description=str(data.get("description", "")),
        version=int(data.get("version", 1)),
        sections=sections,
        prompt=str(data["prompt"]),
        output=output,
    )


def _load_yaml_file(filepath: Path) -> NoteTemplate:
    """加载单个 YAML 模板文件。

    Raises:
        TemplateError: YAML 解析失败或内容格式错误。
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise TemplateError(filepath.stem, f"YAML 解析失败：{e}") from e
    except OSError as e:
        raise TemplateError(filepath.stem, f"无法读取文件：{e}") from e

    if not isinstance(data, dict):
        raise TemplateError(filepath.stem, "YAML 文件必须是顶层字典")

    return _parse_template_yaml(data)


# ── TemplateRegistry ─────────────────────────────────────────


class TemplateRegistry:
    """模板注册中心。

    管理内置模板和用户自定义模板的加载、查询。

    Usage::

        registry = TemplateRegistry()
        registry.load_builtin()
        template = registry.get("study")
        for t in registry.list_templates():
            print(t.id, t.name)
    """

    def __init__(self) -> None:
        self._templates: dict[str, NoteTemplate] = {}

    # ── 加载 ───────────────────────────────────────────────

    def load_builtin(self, templates_dir: Path | None = None) -> int:
        """加载所有内置 YAML 模板。

        Args:
            templates_dir: 模板目录路径（默认 src/core/notes/templates/）。

        Returns:
            成功加载的模板数量。
        """
        if templates_dir is None:
            templates_dir = _BUILTIN_TEMPLATES_DIR

        count = 0
        if not templates_dir.is_dir():
            logger.warning(
                "Built-in template directory does not exist: %s",
                templates_dir,
            )
            return count

        for filepath in sorted(templates_dir.glob("*.yaml")):
            try:
                template = _load_yaml_file(filepath)
                # 检查 ID 是否与文件名一致
                expected_id = filepath.stem
                if template.id != expected_id:
                    # 允许但给出警告的风格：仍加载，但以文件内容中的 id 为准
                    pass
                self._templates[template.id] = template
                count += 1
            except TemplateError:
                # 跳过加载失败的模板（后续 list 时会显示）
                raise

        return count

    def load_user_templates(self, user_dir: str | Path) -> int:
        """从用户目录加载自定义模板。

        Args:
            user_dir: 包含 .yaml 模板文件的目录。

        Returns:
            成功加载的模板数量。
        """
        user_path = Path(user_dir)
        count = 0
        if not user_path.is_dir():
            return count

        for filepath in sorted(user_path.glob("*.yaml")):
            try:
                template = _load_yaml_file(filepath)
                self._templates[template.id] = template
                count += 1
            except TemplateError:
                raise

        return count

    def register(self, template: NoteTemplate) -> None:
        """手动注册一个模板（覆盖同 ID 已有模板）。"""
        _validate_template_id(template.id)
        self._templates[template.id] = template

    # ── 查询 ───────────────────────────────────────────────

    def get(self, template_id: str) -> NoteTemplate:
        """获取模板。

        Args:
            template_id: 模板 ID。

        Returns:
            NoteTemplate 实例。

        Raises:
            TemplateError: 模板不存在。
        """
        template = self._templates.get(template_id)
        if template is None:
            available = ", ".join(sorted(self._templates.keys()))
            raise TemplateError(
                template_id,
                f"未找到模板 '{template_id}'。可用模板：{available or '(无)'}",
            )
        return template

    def get_or_default(self, template_id: str | None) -> NoteTemplate:
        """获取模板，不存在时回退到 default。"""
        if template_id and template_id in self._templates:
            return self._templates[template_id]
        return self._templates.get("default", _empty_template())

    def list_templates(self) -> list[NoteTemplate]:
        """列出所有已注册模板，按 ID 排序。"""
        return sorted(self._templates.values(), key=lambda t: t.id)

    def template_ids(self) -> list[str]:
        """列出所有模板 ID。"""
        return sorted(self._templates.keys())

    # ── 预览 (V0.5.1) ──────────────────────────────────────

    def preview_template(self, template_id: str) -> str:
        """生成模板的预览文本。

        包含：名称、描述、必需/可选章节、输出选项、Prompt 预览。
        用于 --template-preview 命令。

        Args:
            template_id: 模板 ID。

        Returns:
            人类可读的模板预览文本。

        Raises:
            TemplateError: 模板不存在。
        """
        t = self.get(template_id)

        # 分隔线宽度
        w = 60

        lines = []
        lines.append("=" * w)
        lines.append(f"  模板预览: {t.id}")
        lines.append("=" * w)
        lines.append("")
        lines.append(f"  名称:     {t.name}")
        lines.append(f"  描述:     {t.description}")
        lines.append(f"  版本:     v{t.version}")
        lines.append("")

        # 章节
        required_secs = [s for s in t.sections if s.required]
        optional_secs = [s for s in t.sections if not s.required]

        lines.append("-" * w)
        lines.append("  必需章节:")
        if required_secs:
            for s in required_secs:
                desc = f" — {s.description}" if s.description else ""
                lines.append(f"    • {s.title} ({s.id}){desc}")
        else:
            lines.append("    (无)")
        lines.append("")

        if optional_secs:
            lines.append("  可选章节:")
            for s in optional_secs:
                desc = f" — {s.description}" if s.description else ""
                lines.append(f"    • {s.title} ({s.id}){desc}")
            lines.append("")

        # 输出选项
        lines.append("-" * w)
        lines.append("  输出选项:")
        lines.append(f"    格式:        {t.output.format}")
        lines.append(f"    时间戳:      {'是' if t.output.with_timestamps else '否'}")
        lines.append(f"    引用来源:    {'是' if t.output.with_citations else '否'}")
        if t.output.language:
            lines.append(f"    语言:        {t.output.language}")
        lines.append("")

        # Prompt 预览
        lines.append("-" * w)
        lines.append("  System Prompt 预览:")
        prompt_lines = t.prompt.strip().splitlines()
        if len(prompt_lines) <= 10:
            for pl in prompt_lines:
                lines.append(f"    {pl}")
        else:
            for pl in prompt_lines[:8]:
                lines.append(f"    {pl}")
            lines.append(f"    ... (共 {len(prompt_lines)} 行)")
        lines.append("")
        lines.append("=" * w)

        return "\n".join(lines)

    def __contains__(self, template_id: str) -> bool:
        return template_id in self._templates

    def __len__(self) -> int:
        return len(self._templates)


# ── 回退模板 ─────────────────────────────────────────────────


def _empty_template() -> NoteTemplate:
    """最小化的回退模板，用于没有任何内置模板的情况。"""
    return NoteTemplate(
        id="default",
        name="通用总结",
        description="内置回退模板",
        version=1,
        sections=[
            TemplateSection(id="core_theme", title="核心主题"),
            TemplateSection(id="key_points", title="关键要点"),
            TemplateSection(id="detailed_content", title="详细内容"),
            TemplateSection(id="summary", title="总结"),
        ],
        prompt=(
            "你是一个专业的视频内容分析师。"
            "请根据转写内容生成结构化笔记，"
            "不要编造信息，按章节组织内容，输出 Markdown。"
        ),
        output=TemplateOutputOptions(),
    )


# ── 单例 ─────────────────────────────────────────────────────

_global_registry: TemplateRegistry | None = None


def get_template_registry() -> TemplateRegistry:
    """获取全局 TemplateRegistry 单例（惰性加载内置模板）。"""
    global _global_registry
    if _global_registry is None:
        _global_registry = TemplateRegistry()
        _global_registry.load_builtin()
    return _global_registry


def reset_template_registry() -> None:
    """重置全局注册表（测试用）。"""
    global _global_registry
    _global_registry = None
