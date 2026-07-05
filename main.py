"""video-notes-ai - AI 视频笔记生成工具 (兼容入口)."""

from __future__ import annotations

import multiprocessing
import sys


def _run_private_worker_if_requested() -> int | None:
    """Handle private child-process modes before normal CLI/GUI bootstrap."""
    if "--ocr-worker" not in sys.argv:
        return None
    index = sys.argv.index("--ocr-worker")
    worker_args = sys.argv[index + 1 :]
    from src.app.bootstrap import init_environment

    init_environment()
    from src.infrastructure.video.ocr_worker_cli import main as worker_main

    return int(worker_main(worker_args))



_NO_SESSION_LOG_FLAGS = {
    "--check-ocr",
    "--doctor",
    "--template-list",
    "--template-preview",
    "--template-validate",
    "--template-recommend",
    "--job-list",
    "--job-status",
    "--collection-list",
    "--collection-status",
    "--citation-preview",
}


def _should_install_crash_guard(argv: list[str] | None = None) -> bool:
    """Log GUI/processing sessions, not private workers or read-only probes."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return True
    if "--ocr-worker" in args:
        return False
    return not any(flag in args for flag in _NO_SESSION_LOG_FLAGS)


def main():
    from src.app.cli.main import main as cli_main

    return cli_main()


def process_url(*args, **kwargs):
    from src.application.pipeline.video_pipeline import process_url as _process_url

    return _process_url(*args, **kwargs)


def process_local(*args, **kwargs):
    from src.application.pipeline.video_pipeline import process_local as _process_local

    return _process_local(*args, **kwargs)


def run():
    from src.app.bootstrap import run as _run

    return _run()


__all__ = ["main", "process_url", "process_local", "run"]


if __name__ == "__main__":
    multiprocessing.freeze_support()
    # Install before worker/bootstrap imports so both the GUI and private OCR
    # process suppress consoles created by bundled third-party dependencies.
    from src.utils.subprocess_flags import install_windows_subprocess_guard
    install_windows_subprocess_guard()

    worker_exit = _run_private_worker_if_requested()
    if worker_exit is not None:
        raise SystemExit(worker_exit)

    if _should_install_crash_guard():
        from src.application.diagnostics.crash_guard import install_crash_guard

        install_crash_guard()
    run()
