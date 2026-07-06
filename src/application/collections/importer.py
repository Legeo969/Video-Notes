"""V0.6.1 CollectionImporter — 本地文件夹和 playlist URL 导入器。

支持两种导入模式：
  CollectionFolderImporter  — 扫描文件夹中的音视频文件
  CollectionPlaylistImporter — 使用 yt-dlp 展开 playlist URL
"""

from __future__ import annotations

import os
import json
import re
import subprocess
from src.utils.subprocess_flags import hidden_subprocess_kwargs
from src.utils.external_tools import resolve_tool
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import logging

logger = logging.getLogger(__name__)

# 支持的音视频文件后缀
_SUPPORTED_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v",
    ".mp3", ".wav", ".m4a", ".flac",
})

# 忽略的文件名前缀（隐藏文件 / 临时文件）
_IGNORE_PREFIXES = (".", "~", "._")

SortMode = Literal["name", "mtime", "natural"]


@dataclass
class ImportItem:
    """单个待处理的导入项。"""

    path_or_url: str       # 本地路径 或 URL
    title: str | None      # 从文件名/playlist 提取的标题
    index: int             # 序号（0-based）
    source_type: str       # "file" | "url"

    def __repr__(self) -> str:
        return f"ImportItem(#{self.index} {self.title or self.path_or_url!r})"


def _natural_sort_key(name: str) -> list[str | int]:
    """natural sort key：将字符串拆分为 [str, int, str, int, ...] 序列。

    "2_线性回归.mp4" → ["", 2, "_线性回归", ".mp4"]
    使得 1, 2, 10 正确排序为 1, 2, 10 而不是 1, 10, 2。
    """
    parts = re.split(r"(\d+)", name)
    result: list[str | int] = []
    for part in parts:
        if part.isdigit():
            result.append(int(part))
        else:
            result.append(part.lower())
    return result


class CollectionFolderImporter:
    """扫描本地文件夹，提取支持的音视频文件。"""

    SUPPORTED_EXTENSIONS: frozenset[str] = _SUPPORTED_EXTENSIONS

    def import_folder(
        self,
        folder: str | Path,
        *,
        recursive: bool = False,
        sort: SortMode = "natural",
    ) -> list[ImportItem]:
        """扫描文件夹，返回 ImportItem 列表。

        Args:
            folder: 目标文件夹路径
            recursive: 是否递归扫描子文件夹
            sort: 排序方式 — "name" / "mtime" / "natural"

        Returns:
            按指定排序后的 ImportItem 列表（已过滤隐藏/临时文件）
        """
        folder_path = Path(folder).resolve()
        if not folder_path.is_dir():
            raise FileNotFoundError(f"文件夹不存在: {folder_path}")

        if recursive:
            files_iter = folder_path.rglob("*")
        else:
            files_iter = folder_path.glob("*")

        # 收集所有支持的媒体文件
        media_files: list[Path] = []
        for f in files_iter:
            if not f.is_file():
                continue
            if f.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
                continue
            if self._is_ignored(f, check_parents=recursive):
                continue
            media_files.append(f)

        # 排序
        if sort == "natural":
            media_files.sort(key=lambda p: _natural_sort_key(p.name))
        elif sort == "mtime":
            media_files.sort(key=lambda p: p.stat().st_mtime)
        else:  # "name"
            media_files.sort(key=lambda p: p.name.lower())

        # 构建 ImportItem 列表
        items: list[ImportItem] = []
        for i, file_path in enumerate(media_files):
            title = file_path.stem  # 不含后缀的文件名作为标题
            items.append(ImportItem(
                path_or_url=str(file_path),
                title=title,
                index=i,
                source_type="file",
            ))

        return items

    @staticmethod
    def _is_ignored(file_path: Path, check_parents: bool = False) -> bool:
        """检查是否为隐藏/临时文件。"""
        name = file_path.name
        if name.startswith(_IGNORE_PREFIXES):
            return True
        # 也忽略常见的临时文件后缀
        if name.endswith((".tmp", ".temp", ".crdownload")):
            return True
        # 递归模式下检查父目录是否为隐藏目录
        if check_parents:
            for parent in file_path.parents:
                if parent.name.startswith(_IGNORE_PREFIXES):
                    return True
        return False


class CollectionPlaylistImporter:
    """使用 yt-dlp --flat-playlist 展开 playlist URL。"""

    # 已知的 playlist 站点模式
    _PLAYLIST_SITES = frozenset({
        "youtube.com/playlist",
        "youtube.com/watch",
        "bilibili.com/video",
        "bilibili.com/list",
    })

    def import_playlist(
        self,
        url: str,
        *,
        cookie_file: str | None = None,
    ) -> list[ImportItem]:
        """展开 playlist URL，返回 ImportItem 列表。

        底层使用 yt-dlp --flat-playlist --dump-single-json。
        对缺失 URL 的 entry 跳过并 print warning，不终止整个导入。

        Args:
            url: playlist URL（YouTube/B站等）
            cookie_file: 可选的 cookie 文件路径（用于需要登录的 playlist）

        Returns:
            ImportItem 列表，按 playlist 原始顺序排列

        Raises:
            RuntimeError: yt-dlp 不可用 或 playlist 解析失败
        """
        ytdlp_bin = self._find_ytdlp()
        cmd = [
            ytdlp_bin, url,
            "--flat-playlist",
            "--dump-single-json",
            "--no-warnings",
            "--no-check-certificate",
        ]

        if cookie_file and os.path.isfile(cookie_file):
            cmd.extend(["--cookies", cookie_file])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                **hidden_subprocess_kwargs(),
            )
        except FileNotFoundError:
            raise RuntimeError(
                "yt-dlp 不可用。请在设置 > 插件中安装 download-tools，"
                "或从 https://github.com/yt-dlp/yt-dlp 下载 yt-dlp.exe"
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("playlist 解析超时（120s），请检查 URL 和网络连接")

        if result.returncode != 0:
            stderr = result.stderr.strip() or "未知错误"
            raise RuntimeError(f"playlist 解析失败 (code={result.returncode}): {stderr}")

        # 解析 JSON
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"playlist JSON 解析失败: {e}")

        entries = data.get("entries") or []
        items: list[ImportItem] = []
        skipped = 0

        for i, entry in enumerate(entries):
            if entry is None:
                skipped += 1
                continue

            entry_url = entry.get("url") or entry.get("webpage_url") or ""
            if not entry_url:
                logger.warning(f"⚠️  playlist 第 {i + 1} 项缺少 URL，已跳过: {entry.get('title', 'Unknown')}")
                skipped += 1
                continue

            title = entry.get("title") or f"Video #{i + 1}"
            items.append(ImportItem(
                path_or_url=entry_url,
                title=title,
                index=i,
                source_type="url",
            ))

        if skipped > 0:
            logger.warning(f"⚠️  共跳过 {skipped} 个条目（无 URL 或为 None）")

        return items

    @staticmethod
    def _find_ytdlp() -> str:
        """查找 yt-dlp 可执行文件路径。

        """
        return resolve_tool("yt-dlp", components=["download-tools"], provides="download") or "yt-dlp"

    @staticmethod
    def is_playlist_url(url: str) -> bool:
        """判断 URL 是否为已知的 playlist 格式。"""
        url_lower = url.lower()
        return any(site in url_lower for site in CollectionPlaylistImporter._PLAYLIST_SITES)


def get_supported_extensions() -> frozenset[str]:
    """返回支持的音视频文件后缀（只读）。"""
    return _SUPPORTED_EXTENSIONS
