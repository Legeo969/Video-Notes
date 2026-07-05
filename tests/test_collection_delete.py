"""Tests for collection deletion."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from src.application.collections.schema import initialize_collections
from src.application.collections.service import CollectionService
from src.infrastructure.db.repositories.collection_repository import CollectionRepository
from src.gui.widgets.collection_list_widget import CollectionListWidget

pytestmark = pytest.mark.core

os.environ["QT_QPA_PLATFORM"] = "offscreen"


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


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


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


def test_widget_shows_delete_button(qapp, tmp_path):
    db_path = _create_widget_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    service = CollectionService(conn)
    service.create_collection("Course", collection_id="c1")
    conn.close()

    widget = CollectionListWidget(lambda: str(tmp_path))

    assert any(button.text() == "删除" for button in widget.findChildren(QPushButton))


def test_widget_delete_removes_collection(qapp, tmp_path):
    db_path = _create_widget_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    service = CollectionService(conn)
    service.create_collection("Course", collection_id="c1")
    service.add_job("c1", "j1")
    conn.close()

    widget = CollectionListWidget(lambda: str(tmp_path))
    refresh_count = 0

    def on_refresh():
        nonlocal refresh_count
        refresh_count += 1

    widget.refresh_requested.connect(on_refresh)

    with patch(
        "src.gui.widgets.collection_list_widget.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        widget._on_delete("c1")

    check = sqlite3.connect(db_path)
    assert _count(check, "collections", "c1") == 0
    check.close()
    assert refresh_count == 1


def test_widget_import_folder_adds_media_items(qapp, tmp_path):
    db_path = _create_widget_db(tmp_path)
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    first = media_dir / "01_intro.mp4"
    second = media_dir / "02_audio.wav"
    ignored = media_dir / "notes.txt"
    first.write_text("video")
    second.write_text("audio")
    ignored.write_text("note")

    widget = CollectionListWidget(lambda: str(tmp_path))
    refresh_count = 0

    def on_refresh():
        nonlocal refresh_count
        refresh_count += 1

    widget.refresh_requested.connect(on_refresh)

    with (
        patch(
            "src.gui.widgets.collection_list_widget.QFileDialog.getExistingDirectory",
            return_value=str(media_dir),
        ),
        patch.object(widget, "_input_dialog", return_value=("Imported Course", True)),
        patch("src.gui.widgets.collection_list_widget.QMessageBox.information") as info,
        patch("src.gui.widgets.collection_list_widget.QMessageBox.warning") as warning,
    ):
        widget._on_import_folder()

    warning.assert_not_called()
    info.assert_called_once()

    check = sqlite3.connect(db_path)
    check.row_factory = sqlite3.Row
    collections = check.execute("SELECT * FROM collections").fetchall()
    assert len(collections) == 1
    assert collections[0]["title"] == "Imported Course"
    assert collections[0]["collection_type"] == "folder"

    rows = check.execute(
        """
        SELECT item_index, title, source_uri, status, job_id
        FROM collection_items
        ORDER BY item_index
        """
    ).fetchall()
    check.close()

    assert [row["title"] for row in rows] == ["01_intro", "02_audio"]
    assert [Path(row["source_uri"]).name for row in rows] == [first.name, second.name]
    assert [row["status"] for row in rows] == ["pending", "pending"]
    assert all(row["job_id"].startswith("file:") for row in rows)
    assert refresh_count == 1


def _create_widget_db(tmp_path: Path) -> Path:
    db_dir = tmp_path / ".note_index"
    db_dir.mkdir()
    db_path = db_dir / "video_notes.db"
    conn = sqlite3.connect(db_path)
    initialize_collections(conn)
    conn.commit()
    conn.close()
    return db_path
