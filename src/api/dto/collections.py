"""Collection RPC 数据模型"""

from __future__ import annotations

from pydantic import BaseModel


class CollectionInfo(BaseModel):
    """集合摘要信息。"""

    id: str                        # collection_id (slug)
    name: str                      # 显示名称
    item_count: int = 0
    status: str = "active"         # active / archived


class CollectionDetail(BaseModel):
    """集合详情。"""

    id: str
    name: str
    description: str | None = None
    collection_type: str = "course"
    item_count: int = 0
    completed: int = 0
    failed: int = 0
    pending: int = 0
    status: str = "active"
    created_at: str | None = None
