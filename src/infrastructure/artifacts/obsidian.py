"""ObsidianArchiver — 将笔记归档到 Obsidian vault 的核心逻辑

用法：
from src.infrastructure.artifacts.archive_policy import ArchivePolicy
    from src.infrastructure.artifacts.obsidian import ObsidianArchiver
    archiver = ObsidianArchiver(ArchivePolicy())
    archiver.archive("output/notes.md", "/path/to/vault", "视频标题")
"""

import logging
import os
import re
import shutil
from datetime import datetime
from urllib.parse import unquote

from src.infrastructure.artifacts.archive_policy import ArchivePolicy

logger = logging.getLogger(__name__)


# ── 辅助函数（从 vault_writer 迁移） ─────────────────────────────


def _sanitize_filename(name: str) -> str:
    """将标题转为文件系统安全的文件名"""
    safe = re.sub(r'[\\/:*?"<>|]', "", name)
    safe = safe.replace(" ", "_")
    safe = safe.strip().rstrip(".")
    return safe if safe else "untitled"


def _collect_vault_tags(vault_path: str, max_files: int = 500) -> set[str]:
    """扫描 Obsidian vault，收集所有已有标签

    支持两种标签格式：
    - frontmatter tags 字段: tags: [tag1, tag2] 或 tags:\\n  - tag1
    - 行内标签: #tag-name
    """
    tags: set[str] = set()
    scanned = 0
    skip_dirs = {".obsidian", ".trash", ".git", "node_modules", "__pycache__"}

    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue
            scanned += 1

            # tags: [a, b, c]
            fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
            if fm_match:
                fm = fm_match.group(1)
                arr_match = re.search(r"tags:\s*\[([^\]]+)\]", fm)
                if arr_match:
                    for t in arr_match.group(1).split(","):
                        t = t.strip().strip('"').strip("'")
                        if t:
                            tags.add(t.lower())
                list_match = re.findall(
                    r"^tags?:\s*\n((?:\s+-\s+.*\n?)+)", fm, re.MULTILINE
                )
                for block in list_match:
                    for line in block.split("\n"):
                        m = re.match(r"\s*-\s+(.*)", line)
                        if m:
                            t = m.group(1).strip().strip('"').strip("'")
                            if t:
                                tags.add(t.lower())

            # 行内标签 #TagName
            inline_tags = re.findall(
                r"(?:^|[ \t(\[{])(#[A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff-]*)",
                content,
            )
            for t in inline_tags:
                tag = t.strip("#").lower()
                if len(tag) >= 2 and not tag.isdigit() and not tag.startswith("http"):
                    tags.add(tag)

            if scanned >= max_files:
                break
        if scanned >= max_files:
            break

    return tags


# 模块级缓存，避免每次归档重新扫描 vault
_COLLECTED_TAGS: set[str] | None = None
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((<[^>]+>|[^)]+)\)")


def _match_tags(content: str, vault_tags: set[str]) -> list[str]:
    """根据笔记内容匹配最相关的 vault 标签"""
    content_lower = content.lower()
    scored: list[tuple[str, int]] = []
    for tag in vault_tags:
        count = content_lower.count(tag.lower())
        if count > 0:
            scored.append((tag, count))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored[:8]]


def _make_obsidian_frontmatter(
    title: str, note_content: str = "", vault_path: str | None = None
) -> str:
    """生成 Obsidian 兼容的 YAML frontmatter"""
    today = datetime.now().strftime("%Y-%m-%d")
    safe_title = title.replace('"', '\\"')

    all_tags = ["video-notes"]
    if vault_path and note_content:
        global _COLLECTED_TAGS
        if _COLLECTED_TAGS is None:
            _COLLECTED_TAGS = _collect_vault_tags(vault_path)
        matched = _match_tags(note_content, _COLLECTED_TAGS)
        all_tags.extend(matched)

    tags_str = ", ".join(all_tags)
    return (
        f'---\ntitle: "{safe_title}"\ndate: {today}\n'
        f"tags: [{tags_str}]\nsource: video-notes-ai\n---\n\n"
    )


def _normalize_frame_link(raw_link: str) -> str | None:
    link = raw_link.strip()
    if link.startswith("<") and link.endswith(">"):
        link = link[1:-1].strip()
    link = link.split("#", 1)[0].split("?", 1)[0]
    link = unquote(link).replace("\\", "/")
    if not link.startswith("frames/") or ".." in link.split("/"):
        return None
    return link


def _normalize_obsidian_frame_links(content: str, notes_path: str) -> str:
    """将笔记中的图片链接归一化为 Obsidian 可识别的相对路径。"""
    source_dir = os.path.dirname(notes_path)

    # Markdown ![](path) 格式
    def replace_md_link(match: re.Match) -> str:
        alt_text = match.group(1)
        link = _normalize_frame_link(match.group(2))
        if not link:
            return match.group(0)
        source_path = os.path.join(source_dir, *link.split("/"))
        if not os.path.isfile(source_path):
            return match.group(0)
        return f"![{alt_text}](<{link}>)"

    content = _MD_IMAGE_RE.sub(replace_md_link, content)

    # Obsidian ![[filename]] 格式 → ![[frames/filename]]
    def replace_wikilink(match: re.Match) -> str:
        filename = match.group(1).strip()
        if "/" in filename or "\\" in filename:
            return match.group(0)
        source_path = os.path.join(source_dir, "frames", filename)
        if os.path.isfile(source_path):
            return f"![[frames/{filename}]]"
        return match.group(0)

    content = re.sub(r"!\[\[([^\]]+)\]\]", replace_wikilink, content)
    return content


def _copy_referenced_frames(content: str, notes_path: str, target_dir: str) -> None:
    """复制笔记中引用的帧图片到 Obsidian vault。"""
    source_dir = os.path.dirname(notes_path)
    target_frames_dir = os.path.join(target_dir, "frames")
    copied = 0
    seen: set[str] = set()

    def _try_copy(link: str) -> None:
        nonlocal copied
        link = link.strip()
        if link in seen:
            return
        seen.add(link)
        # Path traversal prevention
        if ".." in link.split("/") or link.startswith("/") or link.startswith("\\"):
            logger.warning("⚠️  Skipping frame link with path traversal: %s", link)
            return
        if "/" in link:
            parts = link.split("/")
        else:
            parts = ["frames", link]
        source_path = os.path.join(source_dir, *parts)
        if not os.path.isfile(source_path):
            return
        target_path = os.path.join(target_dir, *parts)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copy2(source_path, target_path)
        copied += 1

    # Markdown ![](path) 格式
    for match in _MD_IMAGE_RE.finditer(content):
        link = _normalize_frame_link(match.group(2))
        if link:
            _try_copy(link)

    # Obsidian ![[filename]] 或 ![[frames/filename]] 格式
    for match in re.finditer(r"!\[\[([^\]]+)\]\]", content):
        _try_copy(match.group(1).strip())

    if copied:
        logger.info(f"🖼️  Obsidian 图片已归档: {copied} 张 → {target_frames_dir}")


def _copy_all_frames(notes_path: str, target_dir: str) -> None:
    """复制所有帧图片（当前未被 archive 主流程使用）。"""
    source_frames_dir = os.path.join(os.path.dirname(notes_path), "frames")
    if not os.path.isdir(source_frames_dir):
        return
    target_frames_dir = os.path.join(target_dir, "frames")
    copied = 0
    for root, _dirs, files in os.walk(source_frames_dir):
        for name in files:
            source_path = os.path.join(root, name)
            rel_path = os.path.relpath(source_path, source_frames_dir)
            target_path = os.path.join(target_frames_dir, rel_path)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            shutil.copy2(source_path, target_path)
            copied += 1
    if copied:
        logger.info(f"🖼️  Obsidian 图片已归档: {copied} 张 → {target_frames_dir}")


# ── ObsidianArchiver 类 ─────────────────────────────────────────


class ObsidianArchiver:
    """将 Markdown 笔记归档到 Obsidian vault。

    使用 ArchivePolicy 控制归档行为（只复制引用的帧、链接归一化、frontmatter）。
    """

    def __init__(self, policy: ArchivePolicy | None = None):
        self.policy = policy or ArchivePolicy()

    def archive(self, notes_path: str, vault_path: str, video_title: str) -> bool:
        """将生成的笔记归档到 Obsidian vault。

        流程：
        1. 验证 vault 目录和源文件
        2. 读取笔记内容
        3. 创建 video-notes/ 子目录
        4. 生成目标文件名（防冲突）
        5. 按策略处理（链接归一化、frontmatter）
        6. 写入目标文件
        7. 复制引用的帧图片

        Returns:
            True 成功，False 失败（已打印错误信息）
        """
        # 1. 检查 vault 目录
        if not os.path.isdir(vault_path):
            logger.warning(
                f"⚠️  Obsidian vault 目录不存在，跳过归档: {vault_path}\n"
                f"   笔记文件仍保留在: {notes_path}"
            )
            return False

        # 2. 检查源文件
        if not os.path.isfile(notes_path):
            logger.warning(f"⚠️  笔记文件不存在，跳过归档: {notes_path}")
            return False

        # 3. 读取笔记内容
        try:
            with open(notes_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            logger.warning(f"⚠️  读取笔记文件失败: {e}")
            return False

        if not content.strip():
            logger.warning("⚠️  笔记内容为空，跳过归档")
            return False

        # 4. 创建 video-notes 子目录
        target_dir = os.path.join(vault_path, "video-notes")
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as e:
            logger.warning(f"⚠️  无法创建归档目录 {target_dir}: {e}")
            return False

        # 5. 确定目标文件名（避免覆盖）
        safe_title = _sanitize_filename(video_title) if video_title else "untitled"
        target_name = f"{safe_title}.md"
        target_path = os.path.join(target_dir, target_name)

        if os.path.exists(target_path):
            today_stamp = datetime.now().strftime("%Y%m%d")
            counter = 1
            while True:
                target_name = f"{safe_title}_{today_stamp}_{counter}.md"
                target_path = os.path.join(target_dir, target_name)
                if not os.path.exists(target_path):
                    break
                counter += 1
            logger.info(f"📎 笔记已存在，使用带时间戳的文件名: {target_name}")

        # 6. 按策略处理内容
        if self.policy.normalize_obsidian_links:
            content = _normalize_obsidian_frame_links(content, notes_path)

        if self.policy.include_frontmatter:
            frontmatter = _make_obsidian_frontmatter(
                video_title or "untitled",
                note_content=content,
                vault_path=vault_path,
            )
        else:
            frontmatter = ""

        # 7. 写入文件
        try:
            with open(target_path, "w", encoding="utf-8") as f:
                if frontmatter:
                    f.write(frontmatter)
                f.write(content)
        except OSError as e:
            logger.warning(f"⚠️  写入 Obsidian 笔记失败: {e}")
            return False

        # 8. 复制帧图片（按策略）
        if self.policy.copy_only_referenced_frames:
            _copy_referenced_frames(content, notes_path, target_dir)
        else:
            _copy_all_frames(notes_path, target_dir)

        logger.info(f"📖 笔记已归档到 Obsidian: {target_path}")
        return True