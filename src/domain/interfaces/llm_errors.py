"""Provider 抽象层自定义异常。"""

class ProviderError(Exception):
    """所有 provider 异常基类。"""
    pass

class ProviderAuthError(ProviderError):
    """认证失败（401/403 等）。"""
    pass

class ProviderAPITimeout(ProviderError):
    """API 请求超时。"""
    pass

class ProviderAPIError(ProviderError):
    """API 返回错误响应。
    Attributes:
        status_code: HTTP 状态码，None 表示非 HTTP 错误。
    """
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
