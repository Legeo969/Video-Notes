"""API 服务器主循环。

通过 Content-Length 帧协议从 stdin 读取 JSON-RPC 2.0 请求，
分发给已注册的处理器，并将响应写入 stdout。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from src.api.protocol import (
    Dispatcher,
    read_frame,
    send_response,
    send_error,
)
from src.api.protocol.framing import send_event
from src.api.protocol.version import ENGINE_VERSION, PROTOCOL_VERSION
from src.api.handlers.system import create_system_handlers
from src.api.handlers.process import create_process_handlers
from src.api.handlers.notes import create_notes_handlers
from src.api.handlers.settings import create_settings_handlers
from src.api.handlers.collections import create_collections_handlers
from src.api.handlers.diagnostics import create_diagnostics_handlers
from src.api.event_journal import EventJournal
from src.application.services.orchestrator import PipelineOrchestrator
from src.application.services.job_queue import JobQueue, get_default_db_path
from src.application.services.task_supervisor import TaskSupervisor

logger = logging.getLogger(__name__)

_SHUTDOWN_REQUESTED = False


def _resolve_runtime_output_dir(output_dir: str | None = None) -> str:
    """Return the user-configured output directory for all product RPC state.

    The Tauri sidecar is launched from a private AppData working directory.
    Using ``./output`` there creates a second invisible note index.  When the
    caller has not supplied an explicit output dir, load the user setting so
    process.*, notes.* and collections.* all point at the same database.
    """
    requested = (output_dir or "").strip() or "./output"
    if requested not in {"./output", ".\\output", "output"}:
        return requested
    try:
        from src.config.settings import get_default_export_dir, get_settings_path, load_settings
        configured = str(load_settings(get_settings_path()).get("output_dir") or "").strip()
        if configured:
            return configured
    except Exception:
        pass
    if requested in {"./output", ".\\output", "output"}:
        try:
            from src.config.settings import get_default_export_dir
            return get_default_export_dir()
        except Exception:
            pass
    return requested


def _shutdown() -> None:
    """请求优雅关闭。"""
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True


def create_dispatcher(
    orchestrator: PipelineOrchestrator | None = None,
    job_queue: JobQueue | None = None,
    journal: EventJournal | None = None,
    output_dir: str = "./output",
) -> Dispatcher:
    """创建并注册所有 RPC 处理器。

    Args:
        orchestrator: PipelineOrchestrator 实例（自动创建）。
        job_queue: JobQueue 实例（自动创建）。
        journal: EventJournal 实例（自动创建）。
        output_dir: 输出根目录。

    Returns:
        配置好的 Dispatcher 实例。
    """
    output_dir = _resolve_runtime_output_dir(output_dir)
    db_path = get_default_db_path(output_dir)
    if orchestrator is None:
        orchestrator = PipelineOrchestrator()
    if job_queue is None:
        job_queue = JobQueue(db_path=db_path, output_dir=output_dir)
    if journal is None:
        journal = EventJournal(db_path)

    def _publish_job_event(run_id, stage, message, percent) -> None:
        job = job_queue.get_job(run_id)
        status = job.status if job is not None else stage.value
        payload = {
            "job_id": run_id,
            "stable_job_id": job.job_id if job is not None else None,
            "status": status,
            "stage": stage.value,
            "progress": float(percent),
            "message": message,
            "timestamp": job.heartbeat_at if job is not None else None,
        }
        terminal_events = {
            "completed": "job.completed",
            "failed": "job.failed",
            "interrupted": "job.interrupted",
            "paused": "job.paused",
            "cancelled": "job.cancelled",
        }
        event_type = terminal_events.get(status, "job.progress")
        event_id = journal.append(run_id, event_type, payload)
        payload["event_id"] = event_id
        # One stable frontend subscription for all lifecycle changes.
        send_event("job.progress", payload)

    job_queue.set_progress_callback(_publish_job_event)
    supervisor = TaskSupervisor(orchestrator, job_queue)

    # A new engine process cannot own workers from the previous process. Mark
    # persisted live states as resumable before accepting frontend requests.
    interrupted = job_queue.reconcile_interrupted_jobs()
    if interrupted:
        logger.warning("Marked %d stale job(s) as interrupted", interrupted)

    d = Dispatcher()

    d.register_all(create_system_handlers(shutdown_hook=_shutdown))
    d.register_all(create_process_handlers(
        orchestrator,
        job_queue,
        journal=journal,
        supervisor=supervisor,
    ))
    d.register_all(create_notes_handlers(
        db_path=get_default_db_path(output_dir),
        output_dir=output_dir,
    ))
    d.register_all(create_settings_handlers())
    d.register_all(create_collections_handlers(
        output_dir=output_dir,
        job_queue=job_queue,
        supervisor=supervisor,
    ))
    d.register_all(create_diagnostics_handlers(output_dir=output_dir))

    return d


def run_server(
    dispatcher: Dispatcher | None = None,
    output_dir: str = "./output",
) -> None:
    """启动 API 服务器主循环。

    从 stdin 持续读取帧，分发请求，写入 stdout 响应。
    当 stdin 关闭或收到 system.shutdown 时退出。

    Args:
        dispatcher: 已配置的分发器（自动创建）。
        output_dir: 输出根目录。
    """
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = False
    sys.stdout = sys.stderr

    output_dir = _resolve_runtime_output_dir(output_dir)

    if dispatcher is None:
        dispatcher = create_dispatcher(output_dir=output_dir)

    # 写入 Hello 帧（引擎启动通知）
    from src.api.protocol.framing import write_frame
    write_frame({
        "jsonrpc": "2.0",
        "protocol_version": PROTOCOL_VERSION,
        "method": "engine.hello",
        "params": {
            "engine_version": ENGINE_VERSION,
            "protocol_version": PROTOCOL_VERSION,
        },
    })

    logger.info("Engine server started (protocol v%d)", PROTOCOL_VERSION)

    while not _SHUTDOWN_REQUESTED:
        try:
            request = read_frame()
            if request is None:
                logger.info("stdin closed, shutting down")
                break

            dispatcher.dispatch(request)

        except EOFError:
            logger.info("EOF received, shutting down")
            break
        except KeyboardInterrupt:
            logger.info("Interrupted, shutting down")
            break
        except Exception as exc:
            logger.exception("Fatal error in server loop")
            # 尝试回复错误，但如果 stdout 已损坏则忽略
            try:
                send_error(
                    None,
                    code="SERVER_ERROR",
                    message=f"Server error: {exc}",
                    retryable=False,
                )
            except Exception:
                pass
            break

    logger.info("Engine server stopped")


def main() -> None:
    """CLI 入口点。"""
    import argparse

    parser = argparse.ArgumentParser(description="Video Notes AI Engine Server")
    parser.add_argument("--stdio", action="store_true", help="Run as stdio server")
    parser.add_argument("--output-dir", default="./output", help="Output directory")
    args = parser.parse_args()

    if not args.stdio:
        print("Usage: engine.py --stdio", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    run_server(output_dir=args.output_dir)


if __name__ == "__main__":
    main()
