"""V0.6 Collection 数据库 schema。

幂等迁移：多次调用不会重复创建/修改。
"""

from __future__ import annotations

import sqlite3


def initialize_collections(conn: sqlite3.Connection) -> None:
    """创建/迁移 collections 相关表（幂等）。

    应在 initialize_database() 的迁移链中调用。
    """
    _create_collections_table(conn)
    _create_collection_items_table(conn)
    _create_collection_summaries_table(conn)


# ── 表创建（幂等：IF NOT EXISTS） ──────────────────────────

def _create_collections_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT,
            collection_type TEXT NOT NULL,
            template_id TEXT,
            output_dir TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_collections_collection_id "
        "ON collections(collection_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_collections_type "
        "ON collections(collection_type)"
    )


def _create_collection_items_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            item_index INTEGER NOT NULL,
            title TEXT,
            source_uri TEXT,
            note_path TEXT,
            status TEXT,
            template_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(collection_id, job_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_collection_items_collection "
        "ON collection_items(collection_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_collection_items_job "
        "ON collection_items(job_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_collection_items_index "
        "ON collection_items(collection_id, item_index)"
    )


def _create_collection_summaries_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_id TEXT NOT NULL,
            summary_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(collection_id, summary_type)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_collection_summaries_collection "
        "ON collection_summaries(collection_id)"
    )
