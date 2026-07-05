"""ArchivePolicy — Obsidian 归档策略配置（frozen dataclass）"""

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class ArchivePolicy:
    """控制 Obsidian 归档行为的不可变策略对象。

    Attributes:
        copy_only_referenced_frames: 仅复制笔记中实际引用的帧图片（默认 True）。
        normalize_obsidian_links: 将图片链接归一化为 Obsidian 可识别的格式（默认 True）。
        include_frontmatter: 为笔记追加 YAML frontmatter（默认 True）。
    """

    copy_only_referenced_frames: bool = True
    normalize_obsidian_links: bool = True
    include_frontmatter: bool = True