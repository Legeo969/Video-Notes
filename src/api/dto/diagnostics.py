"""Diagnostics RPC 数据模型"""

from __future__ import annotations

from pydantic import BaseModel


class DiagnosticCheck(BaseModel):
    """单项系统检查结果。"""

    name: str
    status: str                        # passed / failed / warning / skipped
    detail: str = ""


class ComponentInfo(BaseModel):
    """系统组件信息。"""

    name: str
    version: str | None = None
    installed: bool = False
