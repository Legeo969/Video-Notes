"""JSON-RPC 2.0 方法分发器。

将传入的 RPC 请求路由到已注册的处理器。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

from .errors import MethodNotFound, InternalError, RpcError
from .framing import send_response, send_error
from .version import PROTOCOL_VERSION

logger = logging.getLogger(__name__)

HandlerFunc = Callable[[dict[str, Any]], Any]


class Dispatcher:
    """注册并分发 JSON-RPC 2.0 方法调用。"""

    def __init__(self) -> None:
        self._handlers: dict[str, HandlerFunc] = {}

    def register(self, method: str, handler: HandlerFunc) -> None:
        """注册一个 RPC 方法处理器。"""
        if method in self._handlers:
            logger.warning("Overwriting existing handler for %s", method)
        self._handlers[method] = handler

    def register_all(self, handlers: dict[str, HandlerFunc]) -> None:
        """批量注册处理器。"""
        for method, handler in handlers.items():
            self.register(method, handler)

    def dispatch(self, request: dict) -> None:
        """分发一个 RPC 请求并发送响应。

        请求格式 (JSON-RPC 2.0):
            {
                "jsonrpc": "2.0",
                "protocol_version": 1,
                "id": "req-001",
                "method": "system.info",
                "params": {}
            }

        收到通知（无 id）时，不发送响应。
        """
        req_id = request.get("id")
        method_name = request.get("method", "")
        params = request.get("params", {})

        if not isinstance(params, dict):
            params = {}

        # 协议版本检查
        pv = request.get("protocol_version")
        if pv is not None and pv != PROTOCOL_VERSION:
            send_error(
                req_id,
                code="INCOMPATIBLE_VERSION",
                message=f"Protocol version {pv} is not supported (expected {PROTOCOL_VERSION})",
                retryable=False,
            )
            return

        handler = self._handlers.get(method_name)
        if handler is None:
            if req_id is not None:
                send_response(req_id, error={
                    "code": "METHOD_NOT_FOUND",
                    "message": f"Method not found: {method_name}",
                    "details": None,
                    "retryable": False,
                })
            return

        # 通知（无 id）不发送响应
        if req_id is None:
            try:
                handler(params)
            except Exception:
                logger.exception("Notification handler failed: %s", method_name)
            return

        # 请求-响应
        try:
            result = handler(params)
            send_response(req_id, result=result)
        except RpcError as e:
            send_response(req_id, error=e.to_dict())
        except Exception as e:
            logger.exception("Handler error: %s", method_name)
            send_error(req_id, "INTERNAL_ERROR", str(e), retryable=True)