"""工具函数 - 依赖检查、文件安全命名等"""

import logging
import sys
import os
import subprocess
from src.utils.subprocess_flags import hidden_subprocess_kwargs
import shutil
import re

from src.utils.external_tools import resolve_tool, verify_tool

logger = logging.getLogger(__name__)


def _safe_dirname(title: str, max_len: int = 80) -> str:
    """将视频标题转换为文件系统安全的目录名

    - 去除路径分隔符和 Windows 非法字符
    - 替换空格为下划线
    - 截断过长名称
    - 空标题或纯特殊字符时回退为 "untitled"
    """
    # 去除 Windows 非法字符: \ / : * ? " < > |
    safe = re.sub(r'[\\/:*?"<>|]', '', title)
    # 空格转下划线
    safe = safe.replace(' ', '_')
    # 去除首尾空白和点（Windows 目录名不能以点结尾）
    safe = safe.strip().rstrip('.')
    # 截断
    if len(safe) > max_len:
        safe = safe[:max_len].rstrip('_')
    return safe if safe else 'untitled'


# ---------------------------------------------------------------------------
# 依赖检测 — 多路径扫描 + 自动加入 PATH
# ---------------------------------------------------------------------------

def _candidate_dirs(tool: str) -> list[str]:
    """返回 tool 可能的安装目录列表"""
    home = os.path.expanduser("~")
    candidates = []

    if tool == "ffmpeg":
        candidates = [
            r"C:\ffmpeg\bin",
            r"C:\Program Files\ffmpeg\bin",
            r"C:\Program Files (x86)\ffmpeg\bin",
            os.path.join(home, "bin"),
            os.path.join(home, "ffmpeg", "bin"),
            # scoop 默认安装路径
            os.path.join(home, "scoop", "shims"),
            os.path.join(home, "scoop", "apps", "ffmpeg", "current", "bin"),
            # winget / choco 常见路径
            r"C:\tools\ffmpeg\bin",
        ]
    elif tool == "yt-dlp":
        candidates = [
            os.path.join(home, "bin"),
            os.path.join(home, ".local", "bin"),
            # pip --user 安装路径（含 Python314 等版本号）
            os.path.join(home, "AppData", "Roaming", "Python", "Scripts"),
            os.path.join(home, "AppData", "Roaming", "Python", "Python313", "Scripts"),
            os.path.join(home, "AppData", "Roaming", "Python", "Python314", "Scripts"),
            # scoop
            os.path.join(home, "scoop", "shims"),
            os.path.join(home, "scoop", "apps", "yt-dlp", "current"),
        ]
        # 添加当前 Python 环境的 Scripts 目录
        python_dir = os.path.dirname(sys.executable) if sys.executable else ""
        if python_dir:
            candidates.append(os.path.join(python_dir, "Scripts"))
            candidates.append(python_dir)

    return candidates


def _find_tool_on_disk(tool: str) -> str | None:
    """在常见安装路径中扫描工具，找到后验证功能并自动加入 PATH"""
    exe = tool + (".exe" if sys.platform == "win32" else "")

    for d in _candidate_dirs(tool):
        if not os.path.isdir(d):
            continue
        exe_path = os.path.join(d, exe)
        if not os.path.isfile(exe_path):
            continue

        # 验证工具真的可用（避免找到损坏的副本）
        verified = False
        for flag in ("--version", "-version"):
            try:
                result = subprocess.run(
                    [exe_path, flag],
                    capture_output=True, text=True, timeout=10,
                    **hidden_subprocess_kwargs(),
                )
                if result.returncode == 0:
                    verified = True
                    break
            except Exception:
                continue
        if not verified:
            logger.warning(f"⚠️  找到 {exe_path} 但不可用（跳过）")
            continue

        # 仅当前进程生效
        if d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            logger.info(f"📎 已自动将 {d} 加入 PATH（仅当前会话）")
        return d

    return None


def _get_tool_version(tool: str) -> str | None:
    """获取工具版本号，失败返回 None"""
    resolved = resolve_tool(
        tool,
        components=["download-tools"] if tool == "yt-dlp" else ["ffmpeg-tools"],
        provides="download" if tool == "yt-dlp" else "ffmpeg",
    ) or tool
    for flag in ("--version", "-version"):
        try:
            result = subprocess.run(
                [resolved, flag],
                capture_output=True, text=True, timeout=10,
                **hidden_subprocess_kwargs(),
            )
            first_line = (result.stdout or result.stderr).strip().splitlines()[0]
            if first_line:
                return first_line
        except Exception:
            continue
    return None


def _verify_tool(tool: str) -> bool:
    """验证工具命令是否可正常执行

    某些工具（如 Gyan.dev ffmpeg）的 --version 有 bug，
    需要回退到 -version。先试 --version，失败再试 -version。
    """
    return verify_tool(tool)


def check_ffmpeg() -> bool:
    """检查 FFmpeg 是否可用，支持多路径自动检测"""
    if resolve_tool("ffmpeg", components=["ffmpeg-tools"], provides="ffmpeg"):
        return True

    # 扫描常见安装路径
    found_dir = _find_tool_on_disk("ffmpeg")
    if found_dir:
        return True

    logger.error(
        "❌ 未找到 FFmpeg。\n"
        "   推荐一键安装：\n"
        "     winget install Gyan.FFmpeg\n"
        "   或手动下载: https://ffmpeg.org/download.html\n"
        "   安装后无需手动配置环境变量，程序会自动检测。",
    )
    return False


def check_ytdlp() -> bool:
    """检查 yt-dlp 是否可用，支持多路径自动检测"""
    if resolve_tool("yt-dlp", components=["download-tools"], provides="download"):
        return True

    # 扫描常见安装路径
    found_dir = _find_tool_on_disk("yt-dlp")
    if found_dir:
        return True

    logger.error(
        "❌ 未找到 yt-dlp。\n"
        "   请安装：\n"
        "     在设置 > 插件中安装 download-tools\n"
        "   或：\n"
        "     winget install yt-dlp.yt-dlp",
    )
    return False


def check_dependencies() -> None:
    """启动时检查所有依赖，缺少则给出安装提示"""
    missing = []
    if not check_ffmpeg():
        missing.append("ffmpeg")
    if not check_ytdlp():
        missing.append("yt-dlp")

    if missing:
        items = ", ".join(missing)
        raise RuntimeError(
            f"缺少系统依赖: {items}\n"
            f"  FFmpeg: winget install Gyan.FFmpeg\n"
            f"  yt-dlp: 设置 > 插件安装 download-tools，或 winget install yt-dlp.yt-dlp"
        )
