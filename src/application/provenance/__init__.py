"""V0.4 Provenance — 可信知识库与来源追踪。

将每条知识块链接到原始视频、时间戳、转写片段、截图、OCR 结果等证据来源。
"""

from src.application.provenance.models import SourceRef, ProvenanceBlock
from src.application.provenance.schema import (
    initialize_provenance,
    _migrate_provenance_tables,
)
from src.application.provenance.indexer import ProvenanceIndexer, ProvenanceIndexResult
from src.application.provenance.linker import SourceLinker
from src.application.provenance.renderer import CitationRenderer

__all__ = [
    "SourceRef",
    "ProvenanceBlock",
    "initialize_provenance",
    "_migrate_provenance_tables",
    "ProvenanceIndexer",
    "ProvenanceIndexResult",
    "SourceLinker",
    "CitationRenderer",
]
