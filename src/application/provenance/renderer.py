"""CitationRenderer — 为知识块生成带来源引用的 Markdown。

输出格式：
```markdown
## Self-Attention

Transformer 通过 Self-Attention 让每个 token 关注序列中的其他 token。

**来源**
- [00:12:34–00:13:10] 转写片段：讲解 Query、Key、Value 的关系
- [00:13:08] 截图：attention 公式页面
```

---

用法：
    renderer = CitationRenderer()
    markdown = renderer.render_blocks(blocks)

    # 追加到已有笔记
    final = renderer.append_citations(notes_markdown, blocks)
"""

from __future__ import annotations

from src.application.provenance.models import ProvenanceBlock, SourceRef


def _format_timestamp(seconds: float) -> str:
    """秒 → HH:MM:SS 格式。"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_time_range(start: float | None, end: float | None) -> str:
    """格式化时间范围。"""
    if start is None:
        return ""
    start_str = _format_timestamp(start)
    if end is not None:
        return f"[{start_str}–{_format_timestamp(end)}]"
    return f"[{start_str}]"


def _source_kind_label(kind: str) -> str:
    """来源类型的中文标签。"""
    return {
        "transcript": "转写片段",
        "frame": "截图",
        "ocr": "OCR 文本",
        "vision": "视觉分析",
    }.get(kind, kind)


class CitationRenderer:
    """Markdown 来源引用渲染器。

    属性：
        include_timestamps: 是否包含时间戳（默认 True）
        include_quotes: 是否包含引用文本（默认 True）
        max_quote_len: 引用文本最大长度（默认 100 字符）
        max_sources_per_block: 每个块最多显示几条来源（默认 5）
    """

    def __init__(
        self,
        include_timestamps: bool = True,
        include_quotes: bool = True,
        max_quote_len: int = 100,
        max_sources_per_block: int = 5,
    ):
        self.include_timestamps = include_timestamps
        self.include_quotes = include_quotes
        self.max_quote_len = max_quote_len
        self.max_sources_per_block = max_sources_per_block

    # ── 渲染单个块 ─────────────────────────────────────────

    def render_block(self, block: ProvenanceBlock) -> str:
        """渲染单个知识块为 Markdown（含来源引用）。

        Args:
            block: 带 sources 的 ProvenanceBlock。

        Returns:
            Markdown 字符串。
        """
        lines: list[str] = []

        # 标题
        title = block.title or f"知识块 #{block.block_index}"
        lines.append(f"## {title}")
        lines.append("")

        # 内容
        lines.append(block.content.strip())
        lines.append("")

        # 来源引用
        if block.sources:
            lines.append("**来源**")
            for src in block.sources[:self.max_sources_per_block]:
                line = self._render_source_line(src)
                if line:
                    lines.append(line)
            lines.append("")

        return "\n".join(lines)

    def _render_source_line(self, src: SourceRef) -> str:
        """渲染单条来源引用行。"""
        parts: list[str] = ["-"]

        # 时间戳
        if self.include_timestamps and (src.start_time is not None):
            ts = _format_time_range(src.start_time, src.end_time)
            if ts:
                parts.append(ts)

        # 来源类型
        parts.append(f"{_source_kind_label(src.source_kind)}：")

        # 引用文本
        if self.include_quotes and src.quote:
            quote = src.quote.strip()
            if len(quote) > self.max_quote_len:
                quote = quote[:self.max_quote_len] + "…"
            # 转义 Markdown 特殊字符
            quote = quote.replace("\n", " ")
            parts.append(quote)

        # 截图路径
        if src.source_kind == "frame" and src.path:
            parts.append(f"({src.path})")

        return " ".join(parts)

    # ── 渲染多个块 ─────────────────────────────────────────

    def render_blocks(
        self,
        blocks: list[ProvenanceBlock],
        separator: str = "\n---\n\n",
    ) -> str:
        """渲染多个知识块为 Markdown。

        Args:
            blocks: ProvenanceBlock 列表。
            separator: 块之间的分隔符。

        Returns:
            Markdown 字符串。
        """
        if not blocks:
            return ""
        rendered = [self.render_block(b) for b in blocks]
        return separator.join(rendered)

    # ── 追加到已有笔记 ─────────────────────────────────────

    def append_citations(
        self,
        notes_markdown: str,
        blocks: list[ProvenanceBlock],
        section_title: str = "## 来源引用",
    ) -> str:
        """在已有笔记末尾追加来源引用章节。

        Args:
            notes_markdown: 原始笔记 Markdown。
            blocks: ProvenanceBlock 列表。
            section_title: 新增章节的标题。

        Returns:
            追加了来源引用后的 Markdown。
        """
        if not blocks:
            return notes_markdown

        lines = [notes_markdown.rstrip(), "", section_title, ""]

        for block in blocks:
            title = block.title or f"知识块 #{block.block_index}"
            lines.append(f"### {title}")

            for src in block.sources[:self.max_sources_per_block]:
                line = self._render_source_line(src)
                if line:
                    lines.append(line)

            lines.append("")

        return "\n".join(lines)

    # ── 纯文本时间线渲染 ───────────────────────────────────

    def render_timeline(
        self,
        blocks: list[ProvenanceBlock],
        transcript_segments: list[dict] | None = None,
    ) -> str:
        """渲染时间线格式的来源引用。

        按时间顺序排列所有来源引用，适合做视频时间线导航。

        Args:
            blocks: ProvenanceBlock 列表。
            transcript_segments: 可选，带时间戳的转写分段，用于生成详细时间线。

        Returns:
            Markdown 时间线。
        """
        lines: list[str] = ["## 来源时间线", ""]
        entries: list[tuple[float, str]] = []

        # 收集所有有时间戳的来源
        for block in blocks:
            for src in block.sources:
                if src.start_time is not None:
                    ts = _format_timestamp(src.start_time)
                    block_title = block.title or f"知识块 #{block.block_index}"
                    kind = _source_kind_label(src.source_kind)
                    entries.append((
                        src.start_time,
                        f"- **{ts}** [{kind}] {block_title}",
                    ))
                    if src.quote and self.include_quotes:
                        quote = src.quote.strip()[:self.max_quote_len]
                        entries.append((
                            src.start_time,
                            f"  > {quote}",
                        ))

        # 去重并排序
        entries.sort(key=lambda x: x[0])
        seen: set[str] = set()
        for _, line in entries:
            if line not in seen:
                seen.add(line)
                lines.append(line)

        return "\n".join(lines)
