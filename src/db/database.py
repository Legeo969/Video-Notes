"""SQLite connection boundary for future persistent metadata."""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)


def _sqlite_total_size(db_path: str) -> int:
    """Return the on-disk size of the SQLite database and sidecar files."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        path = db_path + suffix
        try:
            total += os.path.getsize(path)
        except OSError:
            pass
    return total


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # 并发安全：WAL 模式允许并发读 + 防止写锁冲突
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def compact_database(db_path: str) -> dict[str, int]:
    """Checkpoint WAL and rebuild the database so freed pages return to disk.

    This is intentionally an explicit maintenance operation rather than a
    startup task: ``VACUUM`` can take noticeable time on a large database and
    requires exclusive access while it rebuilds the file.
    """
    before_bytes = _sqlite_total_size(db_path)
    conn = connect(db_path)
    try:
        # Flush committed WAL pages before VACUUM.  A busy result is harmless;
        # VACUUM will still either obtain the required lock or raise clearly.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        conn.execute("PRAGMA optimize")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        conn.close()
    after_bytes = _sqlite_total_size(db_path)
    return {
        "before_bytes": before_bytes,
        "after_bytes": after_bytes,
        "released_bytes": max(0, before_bytes - after_bytes),
    }


def _migrate_processing_runs(conn: sqlite3.Connection) -> None:
    """幂等 migration：给 processing_runs 表添加 v2 任务队列列。

    新增列：stage, stage_started_at, job_dir, job_id, elapsed_sec,
           frames_count, blocks_count, note_id，以及产品级任务恢复字段。
    """
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(processing_runs)").fetchall()
    }
    additions = {
        "stage":              "TEXT NOT NULL DEFAULT 'pending'",
        "stage_started_at":   "TEXT",
        "job_dir":            "TEXT",
        "job_id":             "TEXT",
        "elapsed_sec":        "REAL DEFAULT 0",
        "frames_count":       "INTEGER DEFAULT 0",
        "blocks_count":       "INTEGER DEFAULT 0",
        "note_id":            "INTEGER",
        "is_hidden":          "INTEGER NOT NULL DEFAULT 0",
        "progress":           "REAL NOT NULL DEFAULT 0",
        "progress_message":   "TEXT",
        "request_json":       "TEXT NOT NULL DEFAULT '{}'",
        "attempt":            "INTEGER NOT NULL DEFAULT 1",
        "parent_run_id":      "INTEGER",
        "last_active_stage":  "TEXT",
        "heartbeat_at":       "TEXT",
        "interrupted_at":     "TEXT",
    }
    for col, definition in additions.items():
        if col not in existing_cols:
            try:
                conn.execute(
                    f"ALTER TABLE processing_runs ADD COLUMN {col} {definition}"
                )
            except sqlite3.OperationalError:
                pass  # 并发条件下列已存在，忽略

    # 同步更新状态为 running 的旧记录（升级到新状态系统）
    conn.execute(
        "UPDATE processing_runs SET stage = 'completed' "
        "WHERE status = 'completed' AND stage = 'pending'"
    )
    conn.execute(
        "UPDATE processing_runs SET stage = 'failed' "
        "WHERE status = 'failed' AND stage = 'pending'"
    )

    # Repair missing/duplicate UUIDs before enforcing uniqueness.  Older builds
    # only created a normal index, so duplicated job_id values were possible.
    rows = conn.execute(
        "SELECT id, job_id FROM processing_runs ORDER BY id"
    ).fetchall()
    seen: set[str] = set()
    for row in rows:
        value = str(row["job_id"] or "").strip()
        if not value or value in seen:
            value = str(uuid.uuid4())
            conn.execute(
                "UPDATE processing_runs SET job_id = ? WHERE id = ?",
                (value, row["id"]),
            )
        seen.add(value)

    conn.execute("DROP INDEX IF EXISTS idx_processing_runs_job_id")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_processing_runs_job_id "
        "ON processing_runs(job_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_processing_runs_stage "
        "ON processing_runs(stage)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_processing_runs_hidden "
        "ON processing_runs(is_hidden, started_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_processing_runs_parent "
        "ON processing_runs(parent_run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_processing_runs_heartbeat "
        "ON processing_runs(status, heartbeat_at)"
    )



def _has_migration(conn: sqlite3.Connection, version: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
    ).fetchone()
    return row is not None


def _backup_before_v12(conn: sqlite3.Connection, db_path: str) -> str | None:
    """Create one consistent SQLite backup before the first V12 migration.

    Copying only ``video_notes.db`` while WAL mode is active can miss committed
    pages that still live in ``-wal``.  SQLite's backup API produces a complete
    snapshot and is safe while the source connection is open.
    """
    if _has_migration(conn, "v12_job_lifecycle"):
        return None
    table_count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "AND name != 'schema_migrations'"
    ).fetchone()[0]
    if not table_count or not os.path.isfile(db_path) or os.path.getsize(db_path) <= 0:
        return None
    backup_path = db_path + ".pre-v12.bak"
    if os.path.exists(backup_path):
        return backup_path
    destination = sqlite3.connect(backup_path)
    try:
        conn.backup(destination)
        destination.commit()
    finally:
        destination.close()
    logger.info("Created pre-V12 database backup: %s", backup_path)
    return backup_path


def _backup_before_v13(conn: sqlite3.Connection, db_path: str) -> str | None:
    """Create a consistent backup before V13 history/output migrations."""
    if _has_migration(conn, "v13_lifecycle_output"):
        return None
    table_count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "AND name != 'schema_migrations'"
    ).fetchone()[0]
    if not table_count or not os.path.isfile(db_path) or os.path.getsize(db_path) <= 0:
        return None
    backup_path = db_path + ".pre-v13.bak"
    if os.path.exists(backup_path):
        return backup_path
    destination = sqlite3.connect(backup_path)
    try:
        conn.backup(destination)
        destination.commit()
    finally:
        destination.close()
    logger.info("Created pre-V13 database backup: %s", backup_path)
    return backup_path


def _repair_v12_integrity(conn: sqlite3.Connection) -> dict[str, int]:
    """Repair legacy rows that cannot be reached by any task/note/collection.

    V11's *clear history* removed ``processing_runs`` and ``.jobs`` but left
    partial source/transcript rows.  Keep provenance when it is still anchored
    by a processing run, a knowledge block, or a collection item; remove only
    completely unreachable legacy rows.
    """
    counts: dict[str, int] = {}

    # Build the anchored job set in a temporary table so every cleanup query
    # uses the same definition and remains efficient on larger databases.
    conn.execute("DROP TABLE IF EXISTS temp.v12_live_job_ids")
    conn.execute("CREATE TEMP TABLE v12_live_job_ids(job_id TEXT PRIMARY KEY)")
    for query in (
        "SELECT job_id FROM processing_runs WHERE job_id IS NOT NULL AND job_id != ''",
        "SELECT job_id FROM knowledge_blocks WHERE job_id IS NOT NULL AND job_id != ''",
        "SELECT ci.job_id FROM collection_items ci "
        "JOIN collections c ON c.collection_id = ci.collection_id "
        "WHERE ci.job_id IS NOT NULL AND ci.job_id != ''",
    ):
        conn.execute(
            "INSERT OR IGNORE INTO v12_live_job_ids(job_id) " + query
        )

    # Delete children first.  These rows are incomplete and have no remaining
    # owner in the application model.
    for table in ("ocr_results", "frame_assets", "transcript_segments", "video_sources"):
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE job_id NOT IN "
            "(SELECT job_id FROM v12_live_job_ids)"
        )
        counts[table] = max(cursor.rowcount, 0)

    cursor = conn.execute(
        "DELETE FROM block_sources WHERE block_id NOT IN "
        "(SELECT id FROM knowledge_blocks)"
    )
    counts["block_sources"] = max(cursor.rowcount, 0)

    # Repair soft references that older schemas could leave dangling.
    cursor = conn.execute(
        "UPDATE ocr_results SET frame_id = NULL "
        "WHERE frame_id IS NOT NULL AND frame_id NOT IN (SELECT id FROM frame_assets)"
    )
    counts["ocr_frame_links"] = max(cursor.rowcount, 0)
    cursor = conn.execute(
        "UPDATE processing_runs SET note_id = NULL "
        "WHERE note_id IS NOT NULL AND note_id NOT IN (SELECT id FROM notes)"
    )
    counts["run_note_links"] = max(cursor.rowcount, 0)
    cursor = conn.execute(
        "UPDATE knowledge_blocks SET note_id_int = NULL "
        "WHERE note_id_int IS NOT NULL AND note_id_int NOT IN (SELECT id FROM notes)"
    )
    counts["block_note_links"] = max(cursor.rowcount, 0)

    cursor = conn.execute(
        "DELETE FROM collection_items WHERE collection_id NOT IN "
        "(SELECT collection_id FROM collections)"
    )
    counts["collection_items"] = max(cursor.rowcount, 0)
    cursor = conn.execute(
        "DELETE FROM collection_summaries WHERE collection_id NOT IN "
        "(SELECT collection_id FROM collections)"
    )
    counts["collection_summaries"] = max(cursor.rowcount, 0)

    conn.execute("DROP TABLE IF EXISTS temp.v12_live_job_ids")
    return counts

def initialize_database(db_path: str) -> None:
    conn = connect(db_path)
    try:
        # Schema migrations table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # End the schema-table transaction before invoking SQLite backup().
        conn.commit()
        needs_v12_migration = not _has_migration(conn, "v12_job_lifecycle")
        needs_v13_migration = not _has_migration(conn, "v13_lifecycle_output")
        if needs_v12_migration:
            _backup_before_v12(conn, db_path)
        if needs_v13_migration:
            _backup_before_v13(conn, db_path)

        # Notes table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rel_path TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Note keywords table (many-to-many)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS note_keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE,
                UNIQUE(note_id, keyword)
            )
            """
        )

        # Processing runs table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processing_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                input_path TEXT NOT NULL,
                title TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                output_path TEXT,
                transcript_path TEXT,
                error_message TEXT,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT
            )
            """
        )

        # Migration: expand processing_runs for task queue (idempotent)
        _migrate_processing_runs(conn)

        # V0.4: provenance tables
        try:
            from src.application.provenance.schema import _migrate_provenance_tables
            _migrate_provenance_tables(conn)
        except Exception as e:
            logger.exception("Provenance schema migration failed: %s", e)
            raise

        # V0.6: collections tables (幂等)
        try:
            from src.application.collections.schema import initialize_collections
            initialize_collections(conn)
        except Exception as e:
            logger.exception("Collections schema initialization failed: %s", e)
            raise

        if needs_v12_migration:
            repaired = _repair_v12_integrity(conn)
            removed = sum(repaired.values())
            if removed:
                logger.info("V12 database integrity repair changed %d legacy rows: %s", removed, repaired)

        # Create indexes for performance
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notes_rel_path ON notes(rel_path)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_note_keywords_keyword ON note_keywords(keyword)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_processing_runs_status ON processing_runs(status)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                job_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES processing_runs(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_events_run_id "
            "ON job_events(run_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_events_job_id "
            "ON job_events(job_id, id)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
            ("v12_job_lifecycle",),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
            ("v13_lifecycle_output",),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
            ("v14_task_runtime",),
        )
        conn.execute("PRAGMA user_version = 14")

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"database integrity check failed: {integrity}")
        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise sqlite3.IntegrityError(
                f"database foreign key check failed: {len(foreign_key_errors)} row(s)"
            )

        conn.commit()
    finally:
        conn.close()


def upsert_note(db_path: str, rel_path: str, title: str = "", content: str = "") -> int:
    """Upsert a note record and return its integer primary key.

    If a note with the same rel_path already exists, updates title/content/updated_at
    and returns the existing id.  Otherwise inserts a new row and returns the new id.

    Args:
        db_path: 数据库文件路径。
        rel_path: 笔记相对路径（唯一键）。
        title: 笔记标题。
        content: 笔记内容（可选，留空也不影响主键返回）。

    Returns:
        notes.id 整数主键。
    """
    conn = connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO notes (rel_path, title, content)
            VALUES (?, ?, ?)
            ON CONFLICT(rel_path) DO UPDATE SET
                title      = excluded.title,
                content    = excluded.content,
                updated_at = CURRENT_TIMESTAMP
            """,
            (rel_path, title, content),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM notes WHERE rel_path = ?", (rel_path,)
        ).fetchone()
        return int(row["id"])
    finally:
        conn.close()


@contextmanager
def connection(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()