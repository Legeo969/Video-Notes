"""V0.4 Provenance 数据库 Schema。

新增表：
  video_sources       — 视频来源元数据
  transcript_segments — 带时间戳的转写分段
  frame_assets        — 抽帧截图索引
  ocr_results         — OCR 识别结果
  block_sources       — 知识块 ↔ 证据来源多对多

扩展表：
  knowledge_blocks    — 新增 note_id_int, job_id, block_index, summary,
                        start_time, end_time, confidence, content_hash 列
"""

from __future__ import annotations

import sqlite3


# ── 建表 SQL ──────────────────────────────────────────────────

_VIDEO_SOURCES_DDL = """
CREATE TABLE IF NOT EXISTS video_sources (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           TEXT NOT NULL,
    source_type      TEXT NOT NULL,        -- url | local
    source_uri       TEXT NOT NULL,
    title            TEXT,
    duration         REAL,
    local_video_path TEXT,
    created_at       TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(job_id)
)
"""

_TRANSCRIPT_SEGMENTS_DDL = """
CREATE TABLE IF NOT EXISTS transcript_segments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         TEXT NOT NULL,
    segment_index  INTEGER NOT NULL,
    start_time     REAL NOT NULL,
    end_time       REAL NOT NULL,
    text           TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(job_id, segment_index)
)
"""

_FRAME_ASSETS_DDL = """
CREATE TABLE IF NOT EXISTS frame_assets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL,
    frame_index     INTEGER NOT NULL,
    timestamp       REAL NOT NULL,
    path            TEXT NOT NULL,
    perceptual_hash TEXT,
    created_at      TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(job_id, frame_index)
)
"""

_OCR_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS ocr_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    frame_id    INTEGER,
    timestamp   REAL,
    text        TEXT NOT NULL,
    confidence  REAL,
    created_at  TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (frame_id) REFERENCES frame_assets(id) ON DELETE SET NULL
)
"""

_BLOCK_SOURCES_DDL = """
CREATE TABLE IF NOT EXISTS block_sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id    INTEGER NOT NULL,
    source_kind TEXT NOT NULL,            -- transcript | frame | ocr | vision
    source_id   INTEGER NOT NULL,
    relevance   REAL DEFAULT 1.0,
    quote       TEXT,
    created_at  TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (block_id) REFERENCES knowledge_blocks(id) ON DELETE CASCADE,
    UNIQUE(block_id, source_kind, source_id)
)
"""

_KNOWLEDGE_BLOCKS_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_blocks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id       TEXT,
    note_id_int   INTEGER,
    job_id        TEXT,
    block_index   INTEGER NOT NULL DEFAULT 0,
    block_type    TEXT NOT NULL DEFAULT 'section',
    title         TEXT,
    content       TEXT NOT NULL DEFAULT '',
    summary       TEXT,
    source_timestamp REAL,
    source_text   TEXT,
    start_time    REAL,
    end_time      REAL,
    confidence    REAL DEFAULT 1.0,
    content_hash  TEXT,
    created_at    TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
)
"""

# ── knowledge_blocks v2 迁移 ──────────────────────────────────

_KB_MIGRATION_COLUMNS = {
    "note_id_int":   "INTEGER",
    "job_id":        "TEXT",
    "block_index":   "INTEGER NOT NULL DEFAULT 0",
    "summary":       "TEXT",
    "start_time":    "REAL",
    "end_time":      "REAL",
    "confidence":    "REAL DEFAULT 1.0",
    "content_hash":  "TEXT",
}


def _migrate_knowledge_blocks(conn: sqlite3.Connection) -> None:
    """幂等迁移：给 knowledge_blocks 添加 V0.4 provenance 列。

    新增列：note_id_int (FK→notes.id), job_id, block_index,
           summary, start_time, end_time, confidence, content_hash

    同时尝试从旧的 note_id (TEXT/rel_path) 回填 note_id_int。
    """
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(knowledge_blocks)").fetchall()
    }

    for col, definition in _KB_MIGRATION_COLUMNS.items():
        if col not in existing_cols:
            conn.execute(
                f"ALTER TABLE knowledge_blocks ADD COLUMN {col} {definition}"
            )

    # 回填 note_id_int：将旧 note_id (TEXT=rel_path) 映射到 notes.id
    if "note_id_int" not in {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(knowledge_blocks)"
        ).fetchall()
    }:
        # note_id_int 刚添加，尝试回填
        pass  # 由 _backfill_note_id_int 处理

    # 回填 block_index：如果 id 有意义则用 id，否则用 0
    # (新导入的记录会用正确的 block_index)

    # 创建索引
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_blocks_job_id "
        "ON knowledge_blocks(job_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_blocks_note_id_int "
        "ON knowledge_blocks(note_id_int)"
    )


def _backfill_note_id_int(conn: sqlite3.Connection) -> int:
    """将 knowledge_blocks.note_id (TEXT/rel_path) 回填到 note_id_int。

    返回成功回填的行数。
    """
    rows = conn.execute(
        """
        UPDATE knowledge_blocks
        SET note_id_int = (
            SELECT n.id FROM notes n
            WHERE n.rel_path = knowledge_blocks.note_id
        )
        WHERE note_id_int IS NULL
          AND note_id IS NOT NULL
          AND note_id != ''
        """
    )
    return rows.rowcount


def _backfill_block_index(conn: sqlite3.Connection) -> int:
    """为已有知识块填充 block_index（按 note_id 分组递增）。"""
    # 获取所有需要回填的 note_id 组
    groups = conn.execute(
        """
        SELECT note_id, id
        FROM knowledge_blocks
        WHERE block_index = 0
        ORDER BY note_id, id
        """
    ).fetchall()

    if not groups:
        return 0

    count = 0
    current_note = None
    idx = 0
    for row in groups:
        note_id = row["note_id"]
        kb_id = row["id"]
        if note_id != current_note:
            current_note = note_id
            idx = 0
        conn.execute(
            "UPDATE knowledge_blocks SET block_index = ? WHERE id = ?",
            (idx, kb_id),
        )
        idx += 1
        count += 1
    return count


# ── 索引 ──────────────────────────────────────────────────────

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_transcript_segments_job "
    "ON transcript_segments(job_id, start_time)",
    "CREATE INDEX IF NOT EXISTS idx_frame_assets_job "
    "ON frame_assets(job_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_ocr_results_job "
    "ON ocr_results(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_block_sources_block "
    "ON block_sources(block_id)",
    "CREATE INDEX IF NOT EXISTS idx_block_sources_kind "
    "ON block_sources(source_kind, source_id)",
]


# ── V0.4.1 迁移 ───────────────────────────────────────────────

def _migrate_block_sources_dedup(conn: sqlite3.Connection) -> None:
    """V0.4.1 迁移：确保 block_sources 有 (block_id, source_kind, source_id) 唯一约束。

    如果旧表没有此约束，创建唯一索引作为等效实现。
    同时清理已有的重复行（保留 id 最小的那行）。
    """
    # 1. 尝试创建唯一索引（幂等）
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_block_sources "
        "ON block_sources(block_id, source_kind, source_id)"
    )

    # 2. 清理已有重复行（保留 id 最小的那个）
    dupes = conn.execute(
        """
        SELECT block_id, source_kind, source_id, COUNT(*) as cnt,
               MIN(id) as keep_id
        FROM block_sources
        GROUP BY block_id, source_kind, source_id
        HAVING cnt > 1
        """
    ).fetchall()

    for row in dupes:
        conn.execute(
            "DELETE FROM block_sources "
            "WHERE block_id = ? AND source_kind = ? AND source_id = ? AND id != ?",
            (row["block_id"], row["source_kind"], row["source_id"], row["keep_id"]),
        )


# ── 公开 API ──────────────────────────────────────────────────


def initialize_provenance(db_path: str) -> None:
    """在给定数据库中初始化所有 provenance 表（幂等）。

    Args:
        db_path: SQLite 数据库文件路径。
    """
    from src.db.database import connect

    conn = connect(db_path)
    try:
        # 1. 新建表
        for ddl in [
            _VIDEO_SOURCES_DDL,
            _TRANSCRIPT_SEGMENTS_DDL,
            _FRAME_ASSETS_DDL,
            _OCR_RESULTS_DDL,
            _KNOWLEDGE_BLOCKS_DDL,
            _BLOCK_SOURCES_DDL,
        ]:
            conn.execute(ddl)

        # 2. 迁移 knowledge_blocks
        _migrate_knowledge_blocks(conn)

        # 3. 回填数据
        _backfill_note_id_int(conn)
        _backfill_block_index(conn)

        # 4. V0.4.1 去重迁移
        _migrate_block_sources_dedup(conn)

        # 5. 索引
        for idx_sql in _INDEXES:
            conn.execute(idx_sql)

        conn.commit()
    finally:
        conn.close()


def _migrate_provenance_tables(conn: sqlite3.Connection) -> None:
    """在已有连接中执行 provenance 迁移（供 initialize_database 调用）。

    与 initialize_provenance 不同，此函数不管理连接生命周期。
    """
    for ddl in [
        _VIDEO_SOURCES_DDL,
        _TRANSCRIPT_SEGMENTS_DDL,
        _FRAME_ASSETS_DDL,
        _OCR_RESULTS_DDL,
        _KNOWLEDGE_BLOCKS_DDL,
        _BLOCK_SOURCES_DDL,
    ]:
        conn.execute(ddl)

    _migrate_knowledge_blocks(conn)
    _backfill_note_id_int(conn)
    _backfill_block_index(conn)
    _migrate_block_sources_dedup(conn)

    for idx_sql in _INDEXES:
        conn.execute(idx_sql)
