"""Shared note-template and content-detail options for GUI and pipelines.

The GUI exposes two independent choices:
- template_id: controls the chapter structure;
- detail_level: controls how much explanation is written inside that structure.

Legacy ``style`` values are still accepted and migrated so old settings continue to
work after upgrading.
"""

from __future__ import annotations

from typing import Final


TEMPLATE_OPTIONS: Final[list[tuple[str, str, str]]] = [
    ("自动推荐", "auto", "根据标题和转录内容自动选择最合适的结构"),
    ("通用总结", "default", "核心主题、关键要点、详细内容和总结"),
    ("学习笔记", "study", "摘要、核心概念和分章节笔记"),
    ("课程讲义", "lecture", "课程信息、学习目标、关键概念和讲义内容"),
    ("编程教程", "coding_tutorial", "教程概述、实现步骤、环境工具和关键代码"),
    ("访谈总结", "interview", "受访人简介、精彩观点和问答记录"),
    ("会议纪要", "meeting", "议题、讨论要点、决策和行动项"),
    ("产品演示", "product_demo", "产品概述、功能介绍和操作流程"),
    ("论文/研究解读", "research", "论文信息、研究方法、关键发现和局限"),
]

DETAIL_LEVEL_OPTIONS: Final[list[tuple[str, str, str]]] = [
    ("精简", "concise", "只保留核心结论和必要步骤"),
    ("标准", "standard", "信息量与可读性平衡"),
    ("详细", "detailed", "补充背景、解释、例子和注意事项"),
]

_VALID_TEMPLATE_IDS = {value for _label, value, _description in TEMPLATE_OPTIONS}
_VALID_DETAIL_LEVELS = {value for _label, value, _description in DETAIL_LEVEL_OPTIONS}

_LEGACY_STYLE_TO_SELECTION: Final[dict[str, tuple[str, str]]] = {
    "默认": ("auto", "standard"),
    "简洁": ("auto", "concise"),
    "详细": ("auto", "detailed"),
    "教程": ("coding_tutorial", "standard"),
    "教程风格": ("coding_tutorial", "standard"),
    "学习笔记": ("study", "standard"),
    "以学习笔记形式": ("study", "standard"),
}


def selection_from_settings(settings: dict | None) -> tuple[str, str]:
    """Return normalized ``(template_id, detail_level)`` from settings.

    New keys win. When they are absent, old ``style`` values are migrated without
    losing the user's intent.
    """
    data = settings or {}
    legacy_style = str(data.get("style", "") or "").strip()
    legacy_template, legacy_detail = _LEGACY_STYLE_TO_SELECTION.get(
        legacy_style, ("auto", "standard")
    )

    template_id = str(data.get("template_id", "") or "").strip()
    if template_id not in _VALID_TEMPLATE_IDS:
        template_id = legacy_template

    detail_level = str(data.get("detail_level", "") or "").strip()
    if detail_level not in _VALID_DETAIL_LEVELS:
        detail_level = legacy_detail

    return template_id, detail_level


def normalize_template_id(value: str | None) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in _VALID_TEMPLATE_IDS else "auto"


def normalize_detail_level(value: str | None) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in _VALID_DETAIL_LEVELS else "standard"


def detail_level_to_style(detail_level: str | None) -> str | None:
    """Map the new detail level to the existing internal prompt style value."""
    normalized = normalize_detail_level(detail_level)
    if normalized == "concise":
        return "简洁"
    if normalized == "detailed":
        return "详细"
    return None


def legacy_style_for_settings(detail_level: str | None) -> str:
    """Persist a compatible legacy ``style`` value for older code paths."""
    normalized = normalize_detail_level(detail_level)
    if normalized == "concise":
        return "简洁"
    if normalized == "detailed":
        return "详细"
    return "默认"


def set_combo_by_data(combo, value: str) -> bool:
    """Set a QComboBox item by user data without importing Qt in this module."""
    for index in range(combo.count()):
        if str(combo.itemData(index)) == value:
            combo.setCurrentIndex(index)
            return True
    return False
