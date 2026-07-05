"""V0.6 Collections / Course Processing 模块。

提供多视频集合管理：创建、归入、状态查询、总览生成、导入、导出。
"""

from .exporter import CollectionExporter, CollectionExportResult
from .importer import (
    CollectionFolderImporter,
    CollectionPlaylistImporter,
    ImportItem,
    get_supported_extensions,
)
from .models import CollectionItem, CollectionRecord, CollectionStatus
from .schema import initialize_collections
from .service import CollectionService
from .renderer import CollectionOverviewRenderer

__all__ = [
    "CollectionExporter",
    "CollectionExportResult",
    "CollectionFolderImporter",
    "CollectionItem",
    "CollectionOverviewRenderer",
    "CollectionPlaylistImporter",
    "CollectionRecord",
    "CollectionService",
    "CollectionStatus",
    "ImportItem",
    "get_supported_extensions",
    "initialize_collections",
]
