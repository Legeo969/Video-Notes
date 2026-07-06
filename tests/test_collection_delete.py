"""Tests for collection deletion and folder import behavior."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.application.collections.importer import CollectionFolderImporter
from src.application.collections.schema import initialize_collections
from src.application.collections.service import CollectionService
from src.infrastructure.db.repositories.collection_repository import CollectionRepository

pytestmark = pytest.mark.core


class TrackingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self):
        self.commit_count += 1
        return super().commit()

    def rollback(self):
        self.rollback_count += 1
        return super().rollback()


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", factory=TrackingConnection)
    connection.row_factory = sqlite3.Row
    initialize_collections(connection)
    yield connection
    connection.close()


def _insert_summary(conn: sqlite3.Connection, collection_id: str) -> None:
    conn.execute(
        """
        INSERT INTO collection_summaries
            (collection_id, summary_type, content, created_at, updated_at)
        VALUES (?, 'overview', 'summary', '2026-01-01T00:00:00Z',
                '2026-01-01T00:00:00Z')
        """,
        (collection_id,),
    )


def _count(conn: sqlite3.Connection, table: str, collection_id: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE collection_id = ?",
        (collection_id,),
    ).fetchone()
    return row[0]


def test_repository_delete_removes_collection_items_and_summaries(conn):
    repo = CollectionRepository(conn)
    repo.insert_collection("c1", "Course")
    repo.insert_item("c1", "j1", item_index=0)
    _insert_summary(conn, "c1")
    conn.commit()

    deleted = repo.delete_collection("c1")

    assert deleted == 1
    assert _count(conn, "collection_summaries", "c1") == 0
    assert _count(conn, "collection_items", "c1") == 0
    assert _count(conn, "collections", "c1") == 0


def test_service_delete_commits_and_returns_true(conn):
    service = CollectionService(conn)
    service.create_collection("Course", collection_id="c1")
    before = conn.commit_count

    deleted = service.delete_collection("c1")

    assert deleted is True
    assert conn.commit_count == before + 1
    assert service.get_collection("c1") is None


def test_folder_importer_adds_media_items_to_collection(conn, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    first = media_dir / "01_intro.mp4"
    second = media_dir / "02_audio.wav"
    ignored = media_dir / "notes.txt"
    first.write_text("video")
    second.write_text("audio")
    ignored.write_text("note")

    imported = CollectionFolderImporter().import_folder(media_dir)
    service = CollectionService(conn)
    service.create_collection(
        "Imported Course",
        collection_type="folder",
        collection_id="imported-course",
    )
    for item in imported:
        service.add_job(
            "imported-course",
            f"file:{item.index}",
            item_index=item.index,
            title=item.title,
            source_uri=item.path_or_url,
            status="pending",
        )

    collections = conn.execute("SELECT * FROM collections").fetchall()
    assert len(collections) == 1
    assert collections[0]["title"] == "Imported Course"
    assert collections[0]["collection_type"] == "folder"

    rows = conn.execute(
        """
        SELECT item_index, title, source_uri, status, job_id
        FROM collection_items
        ORDER BY item_index
        """
    ).fetchall()

    assert [row["title"] for row in rows] == ["01_intro", "02_audio"]
    assert [Path(row["source_uri"]).name for row in rows] == [first.name, second.name]
    assert [row["status"] for row in rows] == ["pending", "pending"]
    assert all(row["job_id"].startswith("file:") for row in rows)
