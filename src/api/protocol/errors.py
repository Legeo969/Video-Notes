"""JSON-RPC 2.0 错误类型"""

from typing import Any


class RpcError(Exception):
    """JSON-RPC 2.0 错误，携带标准错误信封字段。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: Any = None,
        retryable: bool = False,
        http_status: int = 200,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details
        self.retryable = retryable
        self.http_status = http_status
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
        }


# ── 标准错误 ──

class MethodNotFound(RpcError):
    def __init__(self, method: str) -> None:
        super().__init__(code="METHOD_NOT_FOUND", message=f"Method not found: {method}")


class InvalidParams(RpcError):
    def __init__(self, message: str = "Invalid parameters") -> None:
        super().__init__(code="INVALID_PARAMS", message=message)


class InternalError(RpcError):
    def __init__(self, message: str = "Internal error") -> None:
        super().__init__(code="INTERNAL_ERROR", message=message, retryable=True)


class JobNotFound(RpcError):
    def __init__(self, job_id: int) -> None:
        super().__init__(code="JOB_NOT_FOUND", message=f"Job not found: {job_id}")


class JobAlreadyRunning(RpcError):
    def __init__(self) -> None:
        super().__init__(code="JOB_ALREADY_RUNNING", message="已有任务正在运行")


class ProviderTestFailed(RpcError):
    def __init__(self, message: str) -> None:
        super().__init__(code="PROVIDER_TEST_FAILED", message=message)


class ProtocolError(RpcError):
    """协议层错误（帧解析、版本不兼容等）。"""
    def __init__(self, message: str) -> None:
        super().__init__(code="PROTOCOL_ERROR", message=message)