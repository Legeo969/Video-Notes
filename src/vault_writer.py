"""Obsidian vault 归档模块 — 将生成的笔记自动复制到 Obsidian 仓库

用法：
    from src.vault_writer import archive_to_obsidian
    archive_to_obsidian("output/我的视频/notes.md", "/path/to/vault", "我的视频")

注意：
    核心逻辑已迁移至 src.infrastructure.artifacts.obsidian.ObsidianArchiver。
    本模块保留 archive_to_obsidian() 作为向后兼容的公共入口。
"""

from src.infrastructure.artifacts.archive_policy import ArchivePolicy
from src.infrastructure.artifacts.obsidian import ObsidianArchiver


def archive_to_obsidian(
    notes_path: str,
    vault_path: str,
    video_title: str,
) -> bool:
    """将生成的 notes.md 复制到 Obsidian vault 中

    流程：
    1. 验证 vault 目录存在（不存在则警告并返回 False）
    2. 读取 notes.md 内容
    3. 在 vault 下创建 video-notes/ 子目录
    4. 生成目标文件名：{safe_title}.md（冲突时加时间戳后缀）
    5. 追加 Obsidian frontmatter（title + date）
    6. 写入目标文件
    7. 仅复制笔记实际引用的帧图片

    Args:
        notes_path:  源 notes.md 的完整路径
        vault_path:  Obsidian vault 根目录路径
        video_title: 视频标题（用于 frontmatter 和文件名）

    Returns:
        True  复制成功
        False 失败（目录不存在、文件不存在等），已打印错误信息
    """
    archiver = ObsidianArchiver(ArchivePolicy())
    return archiver.archive(notes_path, vault_path, video_title)
