"""V0.5 PromptBuilder — 从模板构造 LLM prompt。

职责：将 NoteTemplate + 转录文本 + 上下文组装成结构化的 system + user prompt。
支持全量模式和分段 chunk 模式。
"""

from __future__ import annotations

from src.domain.models.note_template import NoteContext, NoteTemplate  # noqa: E402


# ── System Prompt 构建 ───────────────────────────────────────


def build_system_prompt(template: NoteTemplate) -> str:
    """构建 system prompt。

    与旧 SYSTEM_PROMPT 完全解耦，模板自行定义角色。
    """
    return template.prompt.strip()


# ── User Prompt 构建（全量模式） ─────────────────────────────


def build_user_prompt(
    template: NoteTemplate,
    transcript: str,
    context: NoteContext,
    frames_text: str = "",
    ocr_text: str = "",
    style: str | None = None,
) -> str:
    """构建完整的 user prompt（适用于短文本或最终合并）。

    结构：
    1. 视频上下文
    2. 必需章节 + 格式要求
    3. 引用/时间戳要求
    4. 截图/OCR 素材
    5. 转录文本
    """
    parts: list[str] = []

    # 1. 视频上下文
    parts.append(_build_context_header(context, template))

    # 2. 章节要求
    parts.append(_build_section_requirements(template))

    # 3. 输出格式要求
    parts.append(_build_format_requirements(template, context))

    # 4. 内容详细度
    parts.append(_build_detail_requirements(style))

    # 5. 素材（截图、OCR）
    material = _build_material_section(frames_text, ocr_text)
    if material:
        parts.append(material)

    # 6. 转录文本
    parts.append(_build_transcript_section(transcript))

    return "\n\n".join(parts)


# ── Chunk Prompt ─────────────────────────────────────────────


def build_chunk_user_prompt(
    template: NoteTemplate,
    chunk: str,
    chunk_info: str,
    context: NoteContext,
    style: str | None = None,
) -> str:
    """构建分段 prompt（适用于长文本逐段处理）。

    与全量 prompt 的区别：
    - 只要求提取局部信息，不做全局总结
    - 保留时间戳和事实原文
    - 按模板章节归类局部内容
    """
    parts: list[str] = []

    parts.append(chunk_info)

    parts.append(
        "你需要根据以下模板结构，从本段转录中提取内容，并归类到对应章节。\n"
        "注意：只提取本段中出现的信息，不要补充或推测。保留时间戳。"
    )

    parts.append(_build_template_structure_summary(template))
    parts.append(_build_detail_requirements(style))

    # 素材
    parts.append(f"## 转录文本（第 {chunk_info.split('/')[0] if '/' in chunk_info else ''} 段）\n\n{chunk}")

    return "\n\n".join(parts)


# ── Merge Prompt ──────────────────────────────────────────────


def build_merge_user_prompt(
    template: NoteTemplate,
    chunks: list[str],
    context: NoteContext,
    style: str | None = None,
) -> str:
    """构建合并 prompt（将多个 chunk 的结果合并为最终笔记）。

    要求：
    - 去重
    - 按模板章节重组
    - 补齐 required sections
    - 保持时间戳
    """
    parts: list[str] = []

    parts.append(_build_context_header(context, template))
    parts.append(
        "以下是各分段的笔记内容。请将它们合并为一份完整的结构化笔记：\n"
        "- 去除重复内容\n"
        "- 按模板要求的章节重新组织\n"
        "- 确保所有必需章节都已填充\n"
        "- 保留重要时间戳\n"
        "- 如果有章节在分段中完全缺失，请注明"
    )

    parts.append(_build_section_requirements(template))
    parts.append(_build_format_requirements(template, context))
    parts.append(_build_detail_requirements(style))

    # 各段内容
    for i, chunk in enumerate(chunks, 1):
        parts.append(f"## 第 {i} 段笔记\n\n{chunk}")

    return "\n\n".join(parts)


# ── 私有辅助 ─────────────────────────────────────────────────


def _build_context_header(context: NoteContext, template: NoteTemplate) -> str:
    """构建视频上下文头部。"""
    lines = ["## 视频信息"]
    if context.title:
        lines.append(f"- 标题：{context.title}")
    if context.source_url:
        lines.append(f"- 来源：{context.source_url}")
    if context.duration:
        mins = int(context.duration // 60)
        secs = int(context.duration % 60)
        lines.append(f"- 时长：{mins} 分 {secs} 秒")
    lines.append(f"- 模板：{template.name}（{template.id}）")
    return "\n".join(lines)


def _build_section_requirements(template: NoteTemplate) -> str:
    """构建章节要求段落。"""
    lines = ["## 输出章节要求", ""]
    for s in template.sections:
        tag = "【必需】" if s.required else "【可选】"
        desc = f" — {s.description}" if s.description else ""
        lines.append(f"- {tag} {s.title}{desc}")
    lines.append("")
    lines.append("请使用 Markdown heading（## 标题）标记每个章节。")
    return "\n".join(lines)


def _build_format_requirements(template: NoteTemplate, context: NoteContext) -> str:
    """构建输出格式要求段落。"""
    lines = ["## 输出格式要求", ""]
    lines.append(f"- 输出格式：{template.output.format}")

    # with_citations 优先级：context > template.output > false
    use_citations = context.with_citations or template.output.with_citations
    if use_citations:
        lines.append("- 请在每个关键知识点后标注来源时间戳（格式：[HH:MM:SS]）")
        lines.append("- 如有对应截图，请在相关位置引用")

    if template.output.with_timestamps:
        lines.append("- 请在章节开头或关键内容旁标注视频时间戳")
    else:
        lines.append("- 不需要标注时间戳")

    if template.output.language:
        lines.append(f"- 输出语言：{template.output.language}")
    else:
        lines.append("- 输出语言：与转录原文保持一致")

    lines.append("- 不要编造转录中没有的信息")
    return "\n".join(lines)



def _build_detail_requirements(style: str | None) -> str:
    """Build detail guidance independently from the selected template."""
    lines = ["## 内容详细度要求", ""]
    if style == "简洁":
        lines.extend([
            "- 采用精简模式：只保留核心结论、必要步骤和关键数据",
            "- 每个要点尽量用 1～2 句话表达",
            "- 删除重复解释、铺垫和非必要例子",
            "- 不得省略模板要求的必需章节",
        ])
    elif style == "详细":
        lines.extend([
            "- 采用详细模式：在事实依据充分时补充背景、原理和上下文",
            "- 对关键步骤解释目的、操作方法、注意事项和可能结果",
            "- 保留有助于理解的例子、术语解释、数据与限制条件",
            "- 不得脱离转录或可靠视觉证据编造内容",
        ])
    else:
        lines.extend([
            "- 采用标准模式：信息完整、层次清晰，避免过度展开",
            "- 关键概念和步骤应有必要解释，但不重复堆砌",
        ])
    return "\n".join(lines)

def _build_material_section(frames_text: str, ocr_text: str) -> str:
    """构建截图/OCR 素材段落。"""
    parts: list[str] = []
    if frames_text:
        parts.append(f"## 视频截图素材\n\n{frames_text}")
    if ocr_text:
        parts.append(f"## 画面文字 (OCR)\n\n{ocr_text}")
    return "\n\n".join(parts)


def _build_transcript_section(transcript: str) -> str:
    """构建转录文本段落。"""
    return f"## 转录文本\n\n{transcript}"


def _build_template_structure_summary(template: NoteTemplate) -> str:
    """构建模板结构的简要描述（用于 chunk prompt）。"""
    section_names = [s.title for s in template.sections]
    return f"模板章节：{' > '.join(section_names)}"
