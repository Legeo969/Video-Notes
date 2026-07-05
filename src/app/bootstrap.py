"""应用启动引导模块"""

from __future__ import annotations

import logging
import os
import sys


# ── 检测是否运行在 Tauri 环境中 ──
_IN_TAURI = bool(os.environ.get("VIDEO_NOTES_IN_TAURI") or hasattr(sys, "_MEIPASS"))


def init_environment():
    """初始化 Windows 子进程策略、CUDA DLL 路径和 dotenv。"""
    # 必须在导入 yt-dlp / PaddleOCR 等第三方运行库之前安装。第三方库内部
    # 启动 ffmpeg、ffprobe 或 Python worker 时也不得弹出控制台窗口。
    try:
        from src.utils.subprocess_flags import install_windows_subprocess_guard
        install_windows_subprocess_guard()
    except Exception:
        # 启动保护不能阻止应用运行；项目自有 subprocess 调用仍有局部隐藏参数。
        pass

    # ── CUDA DLL 路径设置 ──
    if sys.platform == 'win32':
        _dll_dirs = []
        if hasattr(sys, '_MEIPASS'):
            _dll_dirs.append(sys._MEIPASS)
            _dll_dirs.append(os.path.dirname(sys.executable))
        else:
            _dll_dirs.append(os.path.dirname(os.path.abspath(__file__)))

        for _d in _dll_dirs:
            if os.path.isdir(_d):
                try:
                    os.add_dll_directory(_d)
                except (OSError, AttributeError):
                    pass
                os.environ['PATH'] = _d + os.pathsep + os.environ.get('PATH', '')

    # ── 加载 .env 环境变量 ──
    try:
        from dotenv import load_dotenv, find_dotenv
        load_dotenv()
        if hasattr(sys, '_MEIPASS'):
            exe_dir = os.path.dirname(sys.executable)
            env_path = os.path.join(exe_dir, '.env')
            if os.path.isfile(env_path):
                load_dotenv(env_path, override=True)
    except ImportError:
        pass

    # ── 第三方日志级别（启动后尽早设置，减少噪声）──
    try:
        from src.utils.logging import set_third_party_log_levels
        set_third_party_log_levels()
    except ImportError:
        pass



def run():
    """根据命令行参数决定启动 CLI 或 GUI"""
    init_environment()

    args = sys.argv[1:]

    # ── 引擎侧车模式 (Tauri 通过 stdio 启动) ──
    if "--stdio" in args:
        from src.engine import main as engine_main
        engine_main()
        return

    # ── CLI 模式 ──
    if args:
        from src.app.cli.main import main
        sys.argv = [sys.argv[0]] + args
        main()
        return

    # ── 无参数：Tauri 桌面应用为默认入口 ──
    if _IN_TAURI:
        from src.engine import main as engine_main
        engine_main()
        return

    # 开发环境无参数：提示 Tauri 为主入口
    print("Video Notes AI")
    print("=" * 40)
    print("桌面应用: 运行 desktop/npm run tauri dev")
    print("CLI 模式: python main.py <url> [options]")
    print()
    sys.exit(0)
