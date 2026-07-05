"""Content-Length framed JSON-RPC 2.0 传输协议。

Rust Desktop Core 与 Python Engine 之间通过 stdin/stdout 使用此协议通信。
- stdout: 只允许 Content-Length 帧（请求响应 + 事件通知）
- stderr: 普通日志（不参与协议）
- 单帧最大 8 MiB

帧格式：
    Content-Length: <N>\\r\\n
    \\r\\n
    <N bytes of JSON body>
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Callable

from .errors import ProtocolError
from .version import PROTOCOL_VERSION

_MAX_FRAME_SIZE = 8 * 1024 * 1024  # 8 MiB
_STDOUT_LOCK = threading.Lock()
_PROTOCOL_STDOUT = sys.stdout.buffer


def read_frame() -> dict | None:
    """从 stdin 读取一个 Content-Length 帧，返回解析后的 JSON dict。

    返回 None 表示流已关闭（EOF）。
    抛出 ProtocolError 如果帧格式无效或超出大小限制。
    """
    content_length: int | None = None

    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None  # EOF

        line = line.decode("utf-8", errors="replace").rstrip("\r\n")

        if not line:
            # 空行 = 报头结束
            break

        if line.startswith("Content-Length:"):
            raw = line.split(":", 1)[1].strip()
            try:
                content_length = int(raw)
            except ValueError:
                raise ProtocolError(f"Invalid Content-Length: {raw!r}")

    if content_length is None:
        raise ProtocolError("Missing Content-Length header")

    if content_length > _MAX_FRAME_SIZE:
        raise ProtocolError(
            f"Frame too large: {content_length} bytes "
            f"(max {_MAX_FRAME_SIZE})"
        )

    body = sys.stdin.buffer.read(content_length)
    if len(body) != content_length:
        raise ProtocolError(
            f"Unexpected EOF: expected {content_length} bytes, got {len(body)}"
        )

    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise ProtocolError(f"Invalid JSON body: {e}")


def _write_raw(data: bytes) -> None:
    """线程安全地写入 stdout。"""
    with _STDOUT_LOCK:
        _PROTOCOL_STDOUT.write(data)
        _PROTOCOL_STDOUT.flush()


def write_frame(message: dict) -> None:
    """将一个 dict 作为 Content-Length 帧写入 stdout。"""
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    _write_raw(header + body)


def send_response(request_id: int | str | None, result: object = None, error: dict | None = None) -> None:
    """发送 JSON-RPC 响应帧。"""
    msg: dict = {
        "jsonrpc": "2.0",
        "protocol_version": PROTOCOL_VERSION,
    }
    if request_id is not None:
        msg["id"] = request_id
    if error:
        msg["error"] = error
    else:
        msg["result"] = result
    write_frame(msg)


def send_event(method: str, params: dict) -> None:
    """发送 JSON-RPC 事件通知（无 id 字段）。"""
    write_frame({
        "jsonrpc": "2.0",
        "protocol_version": PROTOCOL_VERSION,
        "method": method,
        "params": params,
    })


def send_error(request_id: int | str | None, code: str, message: str, details: object = None, retryable: bool = False) -> None:
    """发送标准错误响应。"""
    send_response(
        request_id=request_id,
        error={
            "code": code,
            "message": message,
            "details": details,
            "retryable": retryable,
        },
    )
