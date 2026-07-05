"""V0.6.1 Collection Import & Output Polish 测试。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.application.collections.importer import (
    CollectionFolderImporter,
    CollectionPlaylistImporter,
    ImportItem,
    _natural_sort_key,
)
from src.application.collections.exporter import CollectionExporter, CollectionExportResult
from src.application.collections.schema import initialize_collections
from src.application.collections.service import CollectionService, generate_collection_id
from src.application.collections.renderer import CollectionOverviewRenderer


pytestmark = pytest.mark.core


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    initialize_collections(conn)
    # Also need knowledge_blocks for renderer
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_blocks (
            id INTEGER PRIMARY KEY,
            note_id_int INTEGER,
            job_id TEXT,
            title TEXT,
            content TEXT,
            block_type TEXT,
            page_number INTEGER,
            created_at TEXT
        )
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def svc(db_conn):
    return CollectionService(db_conn)


@pytest.fixture
def exporter(db_conn, tmp_path):
    return CollectionExporter(db_conn, base_output_dir=str(tmp_path / "output"))


@pytest.fixture
def tmp_media_dir(tmp_path):
    """创建包含各种测试文件的临时目录。"""
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    # 创建支持的媒体文件
    (media_dir / "01_Intro.mp4").write_text("dummy")
    (media_dir / "02_Gradient_Descent.mkv").write_text("dummy")
    (media_dir / "10_Neural_Network.webm").write_text("dummy")
    (media_dir / "lecture.mov").write_text("dummy")
    (media_dir / "review.avi").write_text("dummy")
    (media_dir / "audio_lesson.mp3").write_text("dummy")
    (media_dir / "podcast.wav").write_text("dummy")
    (media_dir / "song.m4a").write_text("dummy")
    (media_dir / "recording.flac").write_text("dummy")
    (media_dir / "slides.m4v").write_text("dummy")

    # 不支持的文件
    (media_dir / "notes.pdf").write_text("dummy")
    (media_dir / "image.png").write_text("dummy")
    (media_dir / "script.txt").write_text("dummy")

    # 隐藏/临时文件
    (media_dir / ".hidden_video.mp4").write_text("dummy")
    (media_dir / "~temp_file.mkv").write_text("dummy")
    (media_dir / "download.crdownload").write_text("dummy")

    return media_dir


@pytest.fixture
def tmp_deep_media_dir(tmp_path):
    """创建带子文件夹的递归测试目录。"""
    root = tmp_path / "course"
    root.mkdir()
    (root / "00_Overview.mp4").write_text("root")

    sub = root / "week1"
    sub.mkdir()
    (sub / "01_Lecture.mp4").write_text("week1-1")
    (sub / "02_Exercise.mp3").write_text("week1-2")

    sub2 = root / "week2"
    sub2.mkdir()
    (sub2 / "03_Advanced.mkv").write_text("week2-1")

    # 隐藏子文件夹
    hidden = root / ".hidden"
    hidden.mkdir()
    (hidden / "secret.mp4").write_text("hidden")

    return root


# ── Natural Sort 测试 ────────────────────────────────────────

class TestNaturalSort:
    def test_sort_numeric(self):
        items = ["10_file.mp4", "2_file.mp4", "1_file.mp4"]
        result = sorted(items, key=_natural_sort_key)
        assert result == ["1_file.mp4", "2_file.mp4", "10_file.mp4"]

    def test_sort_mixed_chinese(self):
        items = ["10_神经网络.mp4", "2_线性回归.mp4", "1_介绍.mp4"]
        result = sorted(items, key=_natural_sort_key)
        assert result == ["1_介绍.mp4", "2_线性回归.mp4", "10_神经网络.mp4"]

    def test_sort_no_numbers(self):
        items = ["c_file.mp4", "a_file.mp4", "b_file.mp4"]
        result = sorted(items, key=_natural_sort_key)
        assert result == ["a_file.mp4", "b_file.mp4", "c_file.mp4"]

    def test_sort_complex(self):
        items = [
            "video_100_final.mp4",
            "video_2_part1.mp4",
            "video_20_part2.mp4",
            "video_1_intro.mp4",
        ]
        result = sorted(items, key=_natural_sort_key)
        assert result == [
            "video_1_intro.mp4",
            "video_2_part1.mp4",
            "video_20_part2.mp4",
            "video_100_final.mp4",
        ]

    def test_sort_same_number_diff_text(self):
        items = ["01_zzz.mp4", "01_aaa.mp4", "01_bbb.mp4"]
        result = sorted(items, key=_natural_sort_key)
        assert result == ["01_aaa.mp4", "01_bbb.mp4", "01_zzz.mp4"]

    def test_sort_case_insensitive(self):
        items = ["B_file.mp4", "a_file.mp4", "C_file.mp4"]
        result = sorted(items, key=_natural_sort_key)
        assert result == ["a_file.mp4", "B_file.mp4", "C_file.mp4"]


# ── Folder Importer 测试 ──────────────────────────────────────

class TestFolderImporter:
    def test_filter_supported_extensions(self, tmp_media_dir):
        importer = CollectionFolderImporter()
        items = importer.import_folder(tmp_media_dir)

        paths = [Path(it.path_or_url).name for it in items]
        assert len(items) == 10  # 10 supported files
        for ext in [".pdf", ".png", ".txt"]:
            assert not any(p.endswith(ext) for p in paths), f"Should filter {ext}"

    def test_ignore_hidden_files(self, tmp_media_dir):
        importer = CollectionFolderImporter()
        items = importer.import_folder(tmp_media_dir)

        paths = [Path(it.path_or_url).name for it in items]
        for hidden in [".hidden_video.mp4", "~temp_file.mkv"]:
            assert hidden not in paths, f"Should ignore {hidden}"

    def test_ignore_temp_files(self, tmp_media_dir):
        importer = CollectionFolderImporter()
        items = importer.import_folder(tmp_media_dir)

        paths = [Path(it.path_or_url).name for it in items]
        assert "download.crdownload" not in paths

    def test_natural_sort_order(self, tmp_media_dir):
        importer = CollectionFolderImporter()
        items = importer.import_folder(tmp_media_dir, sort="natural")

        names = [Path(it.path_or_url).name for it in items]
        # 数字部分的关键文件应按 natural 顺序
        numbered_names = [n for n in names if n[0].isdigit()]
        assert numbered_names[0].startswith("01_")
        assert numbered_names[1].startswith("02_")
        assert numbered_names[2].startswith("10_")

    def test_name_sort(self, tmp_media_dir):
        importer = CollectionFolderImporter()
        items = importer.import_folder(tmp_media_dir, sort="name")

        names = [Path(it.path_or_url).name.lower() for it in items]
        assert names == sorted(names)

    def test_mtime_sort(self, tmp_media_dir):
        importer = CollectionFolderImporter()
        items = importer.import_folder(tmp_media_dir, sort="mtime")
        assert len(items) == 10

    def test_recursive(self, tmp_deep_media_dir):
        importer = CollectionFolderImporter()
        items = importer.import_folder(tmp_deep_media_dir, recursive=True)

        # 应该找到 root + week1 (2) + week2 (1) = 4
        # .hidden 应被忽略
        assert len(items) == 4

        paths = [Path(it.path_or_url).name for it in items]
        assert "00_Overview.mp4" in paths
        assert "01_Lecture.mp4" in paths
        assert "02_Exercise.mp3" in paths
        assert "03_Advanced.mkv" in paths

    def test_recursive_ignores_hidden_dirs(self, tmp_deep_media_dir):
        importer = CollectionFolderImporter()
        items = importer.import_folder(tmp_deep_media_dir, recursive=True)

        paths = [Path(it.path_or_url).name for it in items]
        assert "secret.mp4" not in paths

    def test_non_recursive(self, tmp_deep_media_dir):
        importer = CollectionFolderImporter()
        items = importer.import_folder(tmp_deep_media_dir, recursive=False)

        # 只应该找到根目录的文件，不含子文件夹
        assert len(items) == 1
        assert Path(items[0].path_or_url).name == "00_Overview.mp4"

    def test_folder_not_found(self):
        importer = CollectionFolderImporter()
        with pytest.raises(FileNotFoundError):
            importer.import_folder("/nonexistent/path/xyz")

    def test_empty_folder(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        importer = CollectionFolderImporter()
        items = importer.import_folder(empty_dir)
        assert items == []

    def test_title_from_filename(self, tmp_media_dir):
        importer = CollectionFolderImporter()
        items = importer.import_folder(tmp_media_dir, sort="name")
        intro = [it for it in items if "01_Intro" in it.path_or_url][0]
        assert intro.title == "01_Intro"

    def test_item_indices(self, tmp_media_dir):
        importer = CollectionFolderImporter()
        items = importer.import_folder(tmp_media_dir, sort="natural")
        for i, item in enumerate(items):
            assert item.index == i

    def test_source_type_is_file(self, tmp_media_dir):
        importer = CollectionFolderImporter()
        items = importer.import_folder(tmp_media_dir)
        for item in items:
            assert item.source_type == "file"


# ── Playlist Importer 测试 ────────────────────────────────────

SAMPLE_PLAYLIST_JSON = {
    "title": "Machine Learning Course",
    "entries": [
        {"title": "1. Linear Regression", "url": "https://youtube.com/watch?v=abc123", "id": "abc123"},
        {"title": "2. Gradient Descent", "url": "https://youtube.com/watch?v=def456", "id": "def456"},
        {"title": "3. Neural Networks", "url": "https://youtube.com/watch?v=ghi789", "id": "ghi789"},
    ],
}

SAMPLE_PLAYLIST_MISSING_URL = {
    "title": "Incomplete Playlist",
    "entries": [
        {"title": "Has URL", "url": "https://youtube.com/watch?v=xxx"},
        {"title": "No URL", "id": "yyy"},
        None,
        {"title": "Another URL", "webpage_url": "https://youtube.com/watch?v=zzz"},
    ],
}


class TestPlaylistImporter:
    def test_parse_entries(self, monkeypatch):
        """测试 playlist JSON 解析。"""
        def mock_run(*args, **kwargs):
            class Result:
                returncode = 0
                stdout = json.dumps(SAMPLE_PLAYLIST_JSON)
                stderr = ""
            return Result()

        monkeypatch.setattr("subprocess.run", mock_run)

        importer = CollectionPlaylistImporter()
        items = importer.import_playlist("https://youtube.com/playlist?list=test")

        assert len(items) == 3
        assert items[0].title == "1. Linear Regression"
        assert items[0].source_type == "url"
        assert items[0].index == 0
        assert items[0].path_or_url == "https://youtube.com/watch?v=abc123"

    def test_missing_url_entry(self, monkeypatch, capsys):
        """缺少 URL 的条目应跳过并打印 warning。"""

        def mock_run(*args, **kwargs):
            class Result:
                returncode = 0
                stdout = json.dumps(SAMPLE_PLAYLIST_MISSING_URL)
                stderr = ""
            return Result()

        monkeypatch.setattr("subprocess.run", mock_run)

        importer = CollectionPlaylistImporter()
        items = importer.import_playlist("https://youtube.com/playlist?list=test")

        # 应返回 2 个有效条目（有 url 和 webpage_url 的）
        assert len(items) == 2
        titles = [it.title for it in items]
        assert "Has URL" in titles
        assert "Another URL" in titles
        assert "No URL" not in titles

    def test_empty_playlist(self, monkeypatch):
        def mock_run(*args, **kwargs):
            class Result:
                returncode = 0
                stdout = json.dumps({"title": "Empty", "entries": []})
                stderr = ""
            return Result()

        monkeypatch.setattr("subprocess.run", mock_run)

        importer = CollectionPlaylistImporter()
        items = importer.import_playlist("https://youtube.com/playlist?list=empty")
        assert items == []

    def test_ytdlp_not_available(self, monkeypatch):
        def mock_run(*args, **kwargs):
            raise FileNotFoundError("yt-dlp not found")

        monkeypatch.setattr("subprocess.run", mock_run)

        importer = CollectionPlaylistImporter()
        with pytest.raises(RuntimeError, match="yt-dlp 不可用"):
            importer.import_playlist("https://youtube.com/playlist?list=test")

    def test_ytdlp_error(self, monkeypatch):
        def mock_run(*args, **kwargs):
            class Result:
                returncode = 1
                stdout = ""
                stderr = "ERROR: unable to download"
            return Result()

        monkeypatch.setattr("subprocess.run", mock_run)

        importer = CollectionPlaylistImporter()
        with pytest.raises(RuntimeError, match="playlist 解析失败"):
            importer.import_playlist("https://youtube.com/playlist?list=bad")

    def test_is_playlist_url(self):
        assert CollectionPlaylistImporter.is_playlist_url(
            "https://youtube.com/playlist?list=xxx"
        )
        assert not CollectionPlaylistImporter.is_playlist_url(
            "https://example.com/video.mp4"
        )


# ── Exporter 测试 ────────────────────────────────────────────

class TestExporter:
    def test_export_overview(self, svc, exporter, tmp_path):
        """导出总览应写入正确的路径。"""
        coll = svc.create_collection("Test Course", collection_type="course")

        result = exporter.export_overview(coll.collection_id)
        assert result is not None
        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "Test Course" in content

    def test_export_items_index(self, svc, exporter, db_conn):
        """概念索引应生成。"""
        coll = svc.create_collection("AI Course")
        svc.add_job(coll.collection_id, "job-1", item_index=0, title="Intro")

        # Add a knowledge block
        db_conn.execute(
            "INSERT INTO knowledge_blocks (job_id, title, content, block_type) VALUES (?, ?, ?, ?)",
            ("job-1", "Python", "Python basics", "concept"),
        )
        db_conn.commit()

        result = exporter.export_items_index(coll.collection_id)
        assert result is not None
        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "AI Course" in content
        assert "概念索引" in content

    def test_export_all(self, svc, exporter, tmp_path):
        """完整导出应创建规范目录结构。"""
        coll = svc.create_collection("Full Course", collection_type="course")

        result = exporter.export_all(coll.collection_id)
        assert result.collection_id == coll.collection_id
        assert result.items_total == 0  # no items yet

        # 检查目录结构
        coll_dir = tmp_path / "output" / "collections" / coll.collection_id
        assert coll_dir.is_dir()
        assert (coll_dir / "items").is_dir()
        assert (coll_dir / "assets").is_dir()

        # 检查 meta 文件
        meta_path = coll_dir / ".collection_meta.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["collection_id"] == coll.collection_id
        assert meta["total_items"] == 0

    def test_export_idempotent(self, svc, exporter):
        """重复导出不应报错。"""
        coll = svc.create_collection("NLP Course")
        exporter.export_all(coll.collection_id)
        # 第二次导出不应报错
        result = exporter.export_all(coll.collection_id)
        assert result.collection_id == coll.collection_id

    def test_export_nonexistent_collection(self, exporter):
        result = exporter.export_all("nonexistent-xxx")
        assert "集合不存在" in result.errors[0]

    def test_exporter_links_note_paths(self, svc, exporter, tmp_path):
        """导出应复制 note_path 到 items/。"""
        coll = svc.create_collection("Course With Notes")

        # 创建模拟笔记文件
        note_dir = tmp_path / "jobs" / "j001"
        note_dir.mkdir(parents=True)
        note_file = note_dir / "notes.md"
        note_file.write_text("# Test Note\nContent", encoding="utf-8")

        svc.add_job(
            coll.collection_id, "j001", item_index=0,
            title="第一讲", note_path=str(note_file),
        )

        result = exporter.export_all(coll.collection_id)
        assert result.items_exported == 1

    def test_collection_dir_uses_collection_id(self, exporter, svc):
        """输出目录应使用 collection_id。"""
        coll = svc.create_collection("Machine Learning", collection_type="course")
        coll_dir = exporter._collection_dir(coll.collection_id)

        assert "collections" in str(coll_dir)
        assert coll.collection_id in str(coll_dir)


# ── Job Collections 查询测试 ──────────────────────────────────

class TestJobCollections:
    def test_job_in_collections(self, svc, db_conn):
        """测试 _get_job_collections helper。"""
        # 需要从 cli 导入
        from src.app.cli import _get_job_collections

        coll1 = svc.create_collection("Course A", collection_type="course")
        coll2 = svc.create_collection("Course B", collection_type="course")

        svc.add_job(coll1.collection_id, "job-42", item_index=0)
        svc.add_job(coll2.collection_id, "job-42", item_index=0)

        # _get_job_collections needs a file-based db, not :memory:
        # Create a temp db file
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Re-create tables in file DB
            conn2 = sqlite3.connect(db_path)
            conn2.row_factory = sqlite3.Row
            initialize_collections(conn2)
            conn2.execute("""
                INSERT INTO collections (collection_id, title, description, collection_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
            """, (coll1.collection_id, coll1.title, None, coll1.collection_type))
            conn2.execute("""
                INSERT INTO collections (collection_id, title, description, collection_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
            """, (coll2.collection_id, coll2.title, None, coll2.collection_type))
            conn2.execute("""
                INSERT INTO collection_items (collection_id, job_id, item_index, created_at, updated_at)
                VALUES (?, ?, ?, datetime('now'), datetime('now'))
            """, (coll1.collection_id, "job-42", 0))
            conn2.execute("""
                INSERT INTO collection_items (collection_id, job_id, item_index, created_at, updated_at)
                VALUES (?, ?, ?, datetime('now'), datetime('now'))
            """, (coll2.collection_id, "job-42", 0))
            conn2.commit()
            conn2.close()

            coll_names = _get_job_collections(db_path, "job-42")
            assert len(coll_names) == 2
            assert any("Course A" in cn for cn in coll_names)
            assert any("Course B" in cn for cn in coll_names)

            # 不存在的 job
            coll_names_empty = _get_job_collections(db_path, "job-999")
            assert coll_names_empty == []

        finally:
            os.unlink(db_path)


# ── get_supported_extensions 测试 ─────────────────────────────

class TestSupportedExtensions:
    def test_all_video_extensions(self):
        exts = set(CollectionFolderImporter.SUPPORTED_EXTENSIONS)
        for ext in [".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"]:
            assert ext in exts

    def test_all_audio_extensions(self):
        exts = set(CollectionFolderImporter.SUPPORTED_EXTENSIONS)
        for ext in [".mp3", ".wav", ".m4a", ".flac"]:
            assert ext in exts


# ── generate_collection_id 测试 ───────────────────────────────

class TestCollectionIdGeneration:
    def test_ascii_slug(self):
        assert generate_collection_id("Machine Learning") == "machine-learning"

    def test_cjk_slug(self):
        cid = generate_collection_id("机器学习课程")
        assert cid.startswith("col-")
        assert len(cid) == 12  # col- + 8 hex

    def test_special_chars(self):
        cid = generate_collection_id("AI & ML: The Course!")
        assert "&" not in cid
        assert ":" not in cid
        assert "!" not in cid
