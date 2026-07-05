"""Database infrastructure public API (lazy to avoid compatibility cycles)."""
from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from collections.abc import Iterator


@contextmanager
def get_connection(db_path: str) -> Iterator[sqlite3.Connection]:
    from src.db.database import connect
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def __getattr__(name: str):
    if name == "DatabaseGateway":
        from .gateway import DatabaseGateway
        return DatabaseGateway
    if name == "ProcessingMetadata":
        from .processing_metadata import ProcessingMetadata
        return ProcessingMetadata
    raise AttributeError(name)


__all__ = ["DatabaseGateway", "ProcessingMetadata", "get_connection"]
