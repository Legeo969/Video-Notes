"""V0.6 Collections / Course Processing 测试。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.application.collections.models import (
    CollectionItem,
    CollectionRecord,
    CollectionStatus,
)
from src.application.collections.schema import initialize_collections
from src.application.collections.service import (
    CollectionService,
    generate_collection_id,
)
from src.application.collections.renderer import CollectionOverviewRenderer
from src.domain.types import PipelineRequest


pytestmark = pytest.mark.core


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def db_conn():
    """创建带 collections schema 的临时内存数据库。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    initialize_collections(conn)
    yield conn
    conn.close()


@pytest.fixture
def svc(db_conn):
    """内存数据库的 CollectionService。"""
    return CollectionService(db_conn)


@pytest.fixture
def tmp_job_dir(tmp_path):
    """创建模拟的 job 目录结构。"""
    job_dir = tmp_path / "test-job-001"
    artifacts = job_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (job_dir / "temp").mkdir()
    return job_dir


# ── Slug 生成测试 ─────────────────────────────────────────────

class TestGenerateCollectionId:
    def test_ascii_slug(self):
        assert generate_collection_id("Machine Learning Course") == "machine-learning-course"

    def test_ascii_with_special_chars(self):
        slug = generate_collection_id("React & Vue: A Comparison!")
        assert slug == "react-vue-a-comparison"

    def test_cjk_fallback(self):
        slug = generate_collection_id("机器学习课程")
        assert slug.startswith("col-")
        assert len(slug) == 12  # col- + 8 hex

    def test_mixed_ascii_cjk(self):
        # mixed → not isascii() → hash fallback
        slug = generate_collection_id("AI机器学习")
        assert slug.startswith("col-")

    def test_idempotent(self):
        slug1 = generate_collection_id("Machine Learning 101")
        slug2 = generate_collection_id("Machine Learning 101")
        assert slug1 == slug2

    def test_max_length(self):
        long_title = "a" * 100
        slug = generate_collection_id(long_title)
        assert len(slug) <= 64


# ── Schema 测试 ───────────────────────────────────────────────

class TestCollectionSchema:
    def test_tables_created(self, db_conn):
        tables = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r[0] for r in tables}
        assert "collections" in names
        assert "collection_items" in names
        assert "collection_summaries" in names

    def test_schema_idempotent(self, db_conn):
        """多次调用 initialize_collections 不会报错。"""
        initialize_collections(db_conn)  # 第二次
        initialize_collections(db_conn)  # 第三次
        # 不应抛异常

    def test_unique_collection_id(self, svc):
        svc.create_collection("Test Course")
        with pytest.raises(ValueError, match="已存在"):
            svc.create_collection("Test Course")


# ── Service CRUD 测试 ─────────────────────────────────────────

class TestCollectionService:
    def test_create_collection(self, svc):
        coll = svc.create_collection("My Course", collection_type="course")
        assert coll.collection_id == "my-course"
        assert coll.title == "My Course"
        assert coll.collection_type == "course"

    def test_create_with_custom_id(self, svc):
        coll = svc.create_collection(
            "Custom", collection_id="my-custom-id"
        )
        assert coll.collection_id == "my-custom-id"

    def test_create_with_template(self, svc):
        coll = svc.create_collection("Course", template_id="lecture")
        assert coll.template_id == "lecture"

    def test_create_duplicate_raises(self, svc):
        svc.create_collection("Test")
        with pytest.raises(ValueError, match="已存在"):
            svc.create_collection("Test")

    def test_list_empty(self, svc):
        assert svc.list_collections() == []

    def test_list_multiple(self, svc):
        svc.create_collection("B", collection_id="b-slug")
        svc.create_collection("A", collection_id="a-slug")
        colls = svc.list_collections()
        assert len(colls) == 2
        titles = {c.title for c in colls}
        assert titles == {"A", "B"}

    def test_get_by_id(self, svc):
        svc.create_collection("Course", collection_id="my-course")
        coll = svc.get_collection("my-course")
        assert coll is not None
        assert coll.title == "Course"

    def test_get_by_title(self, svc):
        svc.create_collection("My Course")
        coll = svc.get_collection("My Course")
        assert coll is not None
        assert coll.title == "My Course"

    def test_get_nonexistent(self, svc):
        assert svc.get_collection("no-such") is None


# ── Item 管理测试 ────────────────────────────────────────────

class TestCollectionItems:
    def test_add_job(self, svc):
        svc.create_collection("Course", collection_id="course-1")
        item = svc.add_job("course-1", job_id="job-001")
        assert item.item_index == 0
        assert item.job_id == "job-001"
        assert item.collection_id == "course-1"

    def test_add_job_auto_index(self, svc):
        svc.create_collection("Course", collection_id="c1")
        svc.add_job("c1", job_id="job-001")
        svc.add_job("c1", job_id="job-002")
        svc.add_job("c1", job_id="job-003")
        items = svc.get_items("c1")
        assert len(items) == 3
        for i, item in enumerate(items):
            assert item.item_index == i

    def test_add_job_explicit_index(self, svc):
        svc.create_collection("Course", collection_id="c1")
        item = svc.add_job("c1", job_id="job-005", item_index=5)
        assert item.item_index == 5

    def test_add_job_idempotent(self, svc):
        svc.create_collection("Course", collection_id="c1")
        item1 = svc.add_job("c1", job_id="job-x", title="Original")
        item2 = svc.add_job("c1", job_id="job-x", title="Updated")
        # 同一 (collection_id, job_id) 应该不创建新行
        items = svc.get_items("c1")
        assert len(items) == 1
        # item_index 不应改变
        assert item2.item_index == item1.item_index
        # 但元数据应该更新
        assert item2.title == "Updated"

    def test_add_job_preserves_index_on_update(self, svc):
        svc.create_collection("Course", collection_id="c1")
        svc.add_job("c1", job_id="j1", item_index=3)
        svc.add_job("c1", job_id="j2")
        # 更新 j2 的 title，不应改变 index
        updated = svc.add_job("c1", job_id="j2", title="New Title")
        assert updated.item_index == 4  # max(3, 0) + 1 = 4

    def test_get_items_empty(self, svc):
        svc.create_collection("Course", collection_id="c1")
        assert svc.get_items("c1") == []

    def test_get_items_ordered(self, svc):
        svc.create_collection("Course", collection_id="c1")
        svc.add_job("c1", job_id="j3", item_index=3)
        svc.add_job("c1", job_id="j1", item_index=1)
        svc.add_job("c1", job_id="j2", item_index=2)
        items = svc.get_items("c1")
        indices = [it.item_index for it in items]
        assert indices == [1, 2, 3]


# ── CollectionStatus 测试 ─────────────────────────────────────

class TestCollectionStatus:
    def test_status_empty_collection(self, svc):
        svc.create_collection("Empty", collection_id="empty")
        status = svc.get_status("empty")
        assert status is not None
        assert status.total_items == 0
        assert status.completed == 0

    def test_status_nonexistent(self, svc):
        assert svc.get_status("no-such") is None

    def test_status_aggregates(self, svc, db_conn):
        """状态从 processing_runs 聚合。"""
        # 先创建 processing_runs 表
        db_conn.execute("""
            CREATE TABLE IF NOT EXISTS processing_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                input_path TEXT,
                title TEXT,
                status TEXT,
                output_path TEXT,
                transcript_path TEXT,
                error_message TEXT,
                started_at TEXT,
                completed_at TEXT,
                stage TEXT,
                stage_started_at TEXT,
                job_dir TEXT,
                job_id TEXT,
                elapsed_sec REAL,
                frames_count INTEGER,
                blocks_count INTEGER,
                note_id INTEGER
            )
        """)

        svc.create_collection("Course", collection_id="c1")
        # 插入不同状态的 job
        db_conn.execute(
            "INSERT INTO processing_runs (job_id, status, input_path, job_dir) "
            "VALUES (?, ?, ?, ?)",
            ("j-done", "completed", "v1.mp4", "/tmp/j-done"),
        )
        db_conn.execute(
            "INSERT INTO processing_runs (job_id, status, input_path, job_dir) "
            "VALUES (?, ?, ?, ?)",
            ("j-fail", "failed", "v2.mp4", "/tmp/j-fail"),
        )
        db_conn.execute(
            "INSERT INTO processing_runs (job_id, status, input_path, job_dir) "
            "VALUES (?, ?, ?, ?)",
            ("j-pend", "pending", "v3.mp4", "/tmp/j-pend"),
        )
        db_conn.execute(
            "INSERT INTO processing_runs (job_id, status, input_path, job_dir) "
            "VALUES (?, ?, ?, ?)",
            ("j-canc", "cancelled", "v4.mp4", "/tmp/j-canc"),
        )
        db_conn.commit()

        svc.add_job("c1", job_id="j-done")
        svc.add_job("c1", job_id="j-fail")
        svc.add_job("c1", job_id="j-pend")
        svc.add_job("c1", job_id="j-canc")

        status = svc.get_status("c1")
        assert status.total_items == 4
        assert status.completed == 1
        assert status.failed == 1
        assert status.pending == 1
        assert status.cancelled == 1

    def test_status_unknown_job(self, svc):
        """不在 processing_runs 中的 job 算作 pending。"""
        svc.create_collection("Course", collection_id="c1")
        svc.add_job("c1", job_id="unknown-job")
        status = svc.get_status("c1")
        assert status.pending == 1


# ── PipelineRequest collection_id 测试 ────────────────────────

class TestPipelineRequestCollection:
    def test_collection_id_none_default(self):
        req = PipelineRequest(input="test.mp4")
        assert req.collection_id is None

    def test_collection_id_explicit(self):
        req = PipelineRequest(input="test.mp4", collection_id="my-course")
        assert req.collection_id == "my-course"

    def test_collection_id_in_flat_kwargs(self):
        req = PipelineRequest(
            input="test.mp4",
            collection_id="flat-course",
            output_dir="./out",
        )
        assert req.collection_id == "flat-course"
        assert req.output_dir == "./out"


# ── Overview Renderer 测试 ────────────────────────────────────

class TestOverviewRenderer:
    def _setup_course(self, svc, db_conn):
        """设置一个带 3 个 job 的课程。"""
        # 创建 processing_runs 表
        db_conn.execute("""
            CREATE TABLE IF NOT EXISTS processing_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                input_path TEXT,
                title TEXT,
                status TEXT,
                output_path TEXT,
                transcript_path TEXT,
                error_message TEXT,
                started_at TEXT,
                completed_at TEXT,
                stage TEXT,
                stage_started_at TEXT,
                job_dir TEXT,
                job_id TEXT,
                elapsed_sec REAL,
                frames_count INTEGER,
                blocks_count INTEGER,
                note_id INTEGER
            )
        """)
        # 创建 knowledge_blocks 表
        db_conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id TEXT,
                job_id TEXT,
                block_type TEXT,
                title TEXT,
                content TEXT,
                source_timestamp REAL,
                source_text TEXT,
                metadata TEXT,
                created_at TEXT
            )
        """)

        svc.create_collection(
            "ML Course", collection_id="ml-course",
            collection_type="course", template_id="lecture",
        )

        for i, (jid, status) in enumerate([
            ("j-ml-1", "completed"),
            ("j-ml-2", "completed"),
            ("j-ml-3", "failed"),
        ]):
            db_conn.execute(
                "INSERT INTO processing_runs (job_id, status, input_path, job_dir, "
                "elapsed_sec) VALUES (?, ?, ?, ?, ?)",
                (jid, status, f"v{i+1}.mp4", f"/tmp/{jid}", 1200 + i * 300),
            )
            # 添加 knowledge_blocks
            if status == "completed":
                db_conn.execute(
                    "INSERT INTO knowledge_blocks (job_id, title, block_type) "
                    "VALUES (?, ?, ?)",
                    (jid, f"Concept {i+1}", "concept"),
                )
                db_conn.execute(
                    "INSERT INTO knowledge_blocks (job_id, title, block_type) "
                    "VALUES (?, ?, ?)",
                    (jid, "共享概念", "concept"),
                )
            svc.add_job("ml-course", job_id=jid, title=f"Lesson {i+1}")
        db_conn.commit()

    def test_overview_generates_markdown(self, svc, db_conn):
        self._setup_course(svc, db_conn)
        overview = svc.generate_overview("ml-course")
        assert overview is not None
        assert "# ML Course" in overview
        assert "视频列表" in overview
        assert "每节摘要" in overview
        assert "Lesson 1" in overview
        assert "Lesson 2" in overview
        assert "Lesson 3" in overview

    def test_overview_contains_concept_index(self, svc, db_conn):
        self._setup_course(svc, db_conn)
        overview = svc.generate_overview("ml-course")
        assert "关键概念索引" in overview
        # "共享概念" 出现在两个 completed job 中
        assert "共享概念" in overview

    def test_overview_nonexistent(self, svc):
        assert svc.generate_overview("no-such") is None

    def test_overview_empty_collection(self, svc):
        svc.create_collection("Empty", collection_id="empty")
        overview = svc.generate_overview("empty")
        assert overview is not None
        assert "# Empty" in overview
        assert "视频数**：0" in overview

    def test_overview_has_status_labels(self, svc, db_conn):
        self._setup_course(svc, db_conn)
        overview = svc.generate_overview("ml-course")
        assert "✅" in overview  # completed
        assert "❌" in overview  # failed


# ── Template Validation 集成测试 ──────────────────────────────

class TestCollectionTemplateWarnings:
    def test_template_warnings_counted(self, svc, db_conn, tmp_path):
        """template_validation.json 中的 warnings 应被计入状态。"""
        # 创建表
        db_conn.execute("""
            CREATE TABLE IF NOT EXISTS processing_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, input_path TEXT, title TEXT,
                status TEXT, output_path TEXT, transcript_path TEXT,
                error_message TEXT, started_at TEXT, completed_at TEXT,
                stage TEXT, stage_started_at TEXT, job_dir TEXT, job_id TEXT,
                elapsed_sec REAL, frames_count INTEGER, blocks_count INTEGER,
                note_id INTEGER
            )
        """)

        svc.create_collection("Course", collection_id="c1")

        # 创建有 warnings 的 job
        job_dir = tmp_path / "job-with-warnings"
        artifacts = job_dir / "artifacts"
        artifacts.mkdir(parents=True)
        validation = artifacts / "template_validation.json"
        validation.write_text(json.dumps({
            "template_id": "study",
            "valid": False,
            "warnings": [{"message": "缺少必需章节：核心概念"}],
            "checked_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")

        db_conn.execute(
            "INSERT INTO processing_runs (job_id, status, job_dir) VALUES (?, ?, ?)",
            ("j-warn", "completed", str(job_dir)),
        )
        db_conn.commit()

        svc.add_job("c1", job_id="j-warn")

        status = svc.get_status("c1")
        assert status.template_warnings == 1

    def test_no_warnings_without_validation_file(self, svc, db_conn, tmp_path):
        """没有 template_validation.json 时不计 warning。"""
        db_conn.execute("""
            CREATE TABLE IF NOT EXISTS processing_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, input_path TEXT, title TEXT,
                status TEXT, output_path TEXT, transcript_path TEXT,
                error_message TEXT, started_at TEXT, completed_at TEXT,
                stage TEXT, stage_started_at TEXT, job_dir TEXT, job_id TEXT,
                elapsed_sec REAL, frames_count INTEGER, blocks_count INTEGER,
                note_id INTEGER
            )
        """)

        svc.create_collection("Course", collection_id="c1")

        job_dir = tmp_path / "job-clean"
        job_dir.mkdir()
        db_conn.execute(
            "INSERT INTO processing_runs (job_id, status, job_dir) VALUES (?, ?, ?)",
            ("j-clean", "completed", str(job_dir)),
        )
        db_conn.commit()

        svc.add_job("c1", job_id="j-clean")
        status = svc.get_status("c1")
        assert status.template_warnings == 0


# ── Collection 默认模板测试 ───────────────────────────────────

class TestCollectionDefaultTemplate:
    def test_collection_saves_template(self, svc):
        coll = svc.create_collection("Course", template_id="lecture")
        assert coll.template_id == "lecture"

    def test_item_can_have_different_template(self, svc):
        svc.create_collection("Course", collection_id="c1", template_id="lecture")
        item = svc.add_job("c1", job_id="j1", template_id="meeting")
        assert item.template_id == "meeting"


# ── 集成: orchestrator 自动归入 ───────────────────────────────

class TestOrchestratorCollectionHook:
    def test_pipeline_request_passes_collection_id(self):
        """PipelineRequest 包含 collection_id 时可以正常构造。"""
        req = PipelineRequest(
            input="https://example.com/video.mp4",
            collection_id="my-collection",
            output_dir="./output",
            gpt_model="mimo-v2.5",
        )
        assert req.collection_id == "my-collection"
        assert req.input == "https://example.com/video.mp4"

    def test_pipeline_request_no_collection(self, svc):
        """没有 collection_id 时不应影响正常流程。"""
        req = PipelineRequest(
            input="test.mp4",
            output_dir="./output",
        )
        assert req.collection_id is None
        assert req.output_dir == "./output"
