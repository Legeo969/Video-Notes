"""Notes RPC 数据模型"""

from __future__ import annotations

from pydantic import BaseModel


class NoteInfo(BaseModel):
    """笔记摘要信息。"""

    id: int
    title: str
    path: str
    created_at: str | None = None


class NoteContent(BaseModel):
    """笔记完整内容。"""

    id: int
    title: str
    content: str
    path: str
