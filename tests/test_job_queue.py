"""测试任务队列系统 — JobState / JobQueue / ProcessingMetadata"""

import os
import sqlite3
import tempfile
import shutil
import pytest
from src.domain.job_state import JobState, JobRecord, get_next_stage, get_stage_order, get_stage_artifact, StageManifest
from src.application.services.job_queue import (
    JobQueue,
    get_default_db_path,
    get_default_state_dir,
    get_legacy_jobs_root,
)
from src.infrastructure.db.processing_metadata import ProcessingMetadata


class TestJobState:
    def test_terminal_states(self):
        assert JobState.COMPLETED.is_terminal
        assert JobState.FAILED.is_terminal
        assert JobState.CANCELLED.is_terminal
        assert not JobState.PENDING.is_terminal
        assert not JobState.TRANSCRIBING.is_terminal

    def test_running_states(self):
        assert JobState.TRANSCRIBING.is_running
        assert JobState.GENERATING_NOTES.is_running
        assert JobState.RESOLVING.is_running
        assert not JobState.COMPLETED.is_running
        assert not JobState.FAILED.is_running
        assert not JobState.PENDING.is_running

    def test_labels(self):
        assert JobState.PENDING.label == "等待中"
        assert JobState.TRANSCRIBING.label == "转录中"
        assert JobState.COMPLETED.label == "已完成"
        assert JobState.FAILED.label == "失败"

    def test_string_value(self):
        assert JobState.PENDING.value == "pending"
        assert JobState("transcribing") == JobState.TRANSCRIBING

    def test_get_next_stage(self):
        assert get_next_stage(JobState.RESOLVING) == JobState.DOWNLOADING
        assert get_next_stage(JobState.TRANSCRIBING) == JobState.EXTRACTING_FRAMES
        # last stage → None
        assert get_next_stage(JobState.INDEXING) is None

    def test_stage_order(self):
        order = get_stage_order()
        assert len(order) == 6
        assert order[0] == JobState.RESOLVING
        assert order[-1] == JobState.INDEXING

    def test_stage_artifacts(self):
        # V0.3.1: artifact 路径带 artifacts/ 前缀（相对于 job_dir）
        assert get_stage_artifact(JobState.RESOLVING) == "artifacts/audio.wav"
        assert get_stage_artifact(JobState.TRANSCRIBING) == "artifacts/transcript.json"
        assert get_stage_artifact(JobState.GENERATING_NOTES) == "artifacts/notes.md"


class TestJobRecord:
    def test_from_running(self):
        r = JobRecord(id=1, job_id="abc123", input="test.mp4", status="running", stage="transcribing")
        assert r.state == JobState.TRANSCRIBING
        assert r.is_running

    def test_from_completed(self):
        r = JobRecord(id=2, job_id="def456", input="test.mp4", status="completed", stage="completed")
        assert r.is_completed
        assert not r.is_failed
        assert not r.is_running

    def test_from_failed(self):
        r = JobRecord(id=3, job_id="ghi789", input="test.mp4", status="failed", stage="failed", error_message="boom")
        assert r.is_failed
        assert r.error_message == "boom"


class TestProcessingMetadata:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "test.db")
        self.meta = ProcessingMetadata(self.db)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_start_and_complete(self):
        rid = self.meta.start_run("input.mp4", title="T", job_dir=self.tmp, job_id="j1")
        assert rid > 0

        # Check start state
        job = self.meta.get_job(rid)
        assert job.status == "running"
        assert job.stage == "pending"

        # Complete
        self.meta.complete_run(rid, output_path="out.md", elapsed_sec=10.0, frames_count=5, note_id=42)
        job = self.meta.get_job(rid)
        assert job.status == "completed"
        assert job.stage == "completed"
        assert job.output_path == "out.md"
        assert job.elapsed_sec == 10.0
        assert job.frames_count == 5
        assert job.note_id == 42

    def test_stage_lifecycle(self):
        rid = self.meta.start_run("v.mp4", job_id="j2")
        self.meta.update_stage(rid, JobState.DOWNLOADING)
        job = self.meta.get_job(rid)
        assert job.stage == "downloading"

        self.meta.update_stage(rid, JobState.TRANSCRIBING)
        job = self.meta.get_job(rid)
        assert job.stage == "transcribing"

    def test_fail(self):
        rid = self.meta.start_run("v.mp4", job_id="j3")
        self.meta.fail_run(rid, "connection refused")
        job = self.meta.get_job(rid)
        assert job.status == "failed"
        assert job.stage == "failed"
        assert "connection refused" in job.error_message

    def test_cancel(self):
        rid = self.meta.start_run("v.mp4", job_id="j4")
        self.meta.cancel_run(rid)
        job = self.meta.get_job(rid)
        assert job.status == "cancelled"
        assert job.stage == "cancelled"

    def test_get_job_by_job_id(self):
        rid = self.meta.start_run("v.mp4", job_id="unique-123")
        job = self.meta.get_job_by_job_id("unique-123")
        assert job is not None
        assert job.id == rid
        assert job.job_id == "unique-123"

    def test_get_job_not_found(self):
        assert self.meta.get_job(9999) is None
        assert self.meta.get_job_by_job_id("nonexistent") is None

    def test_list_jobs_pagination(self):
        for i in range(5):
            self.meta.start_run(f"v{i}.mp4", job_id=f"j{i}")
        jobs = self.meta.list_jobs(limit=3)
        assert len(jobs) == 3

    def test_list_jobs_filter_status(self):
        self.meta.start_run("v1.mp4", job_id="j1")
        rid = self.meta.start_run("v2.mp4", job_id="j2")
        self.meta.complete_run(rid)

        running = self.meta.list_jobs(status="running")
        completed = self.meta.list_jobs(status="completed")
        assert len(running) == 1
        assert len(completed) == 1

    def test_count_jobs(self):
        assert self.meta.count_jobs() == 0
        self.meta.start_run("v1.mp4", job_id="j1")
        rid = self.meta.start_run("v2.mp4", job_id="j2")
        self.meta.complete_run(rid)
        assert self.meta.count_jobs() == 2
        assert self.meta.count_jobs("completed") == 1
        assert self.meta.count_jobs("running") == 1

    def test_get_resumable_completed_is_none(self):
        rid = self.meta.start_run("v.mp4", job_id="j1", job_dir=self.tmp)
        self.meta.complete_run(rid)
        assert self.meta.get_resumable_stage(rid) is None

    def test_get_resumable_fresh_task(self):
        rid = self.meta.start_run("v.mp4", job_id="j1", job_dir=self.tmp)
        # No artifacts exist → return first stage
        stage = self.meta.get_resumable_stage(rid)
        assert stage == JobState.RESOLVING


class TestJobQueue:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "test.db")
        self.jq = JobQueue(self.db, output_dir=self.tmp)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_enqueue_complete_cycle(self):
        rid = self.jq.enqueue("https://example.com/v.mp4", title="My Video")
        assert rid > 0

        job = self.jq.get_job(rid)
        assert job.title == "My Video"
        assert job.input == "https://example.com/v.mp4"
        assert job.job_dir is not None
        assert os.path.isdir(job.job_dir)

        self.jq.update_stage(rid, JobState.TRANSCRIBING, "transcribing...", 30)
        self.jq.complete(rid, notes_path="/out/notes.md", elapsed_sec=99.0)

        job = self.jq.get_job(rid)
        assert job.status == "completed"
        assert job.elapsed_sec == 99.0
        assert job.job_dir is None

    def test_fail_and_cancel(self):
        rid = self.jq.enqueue("v.mp4")
        self.jq.fail(rid, "disk full")
        job = self.jq.get_job(rid)
        assert job.status == "failed"
        assert "disk full" in job.error_message

        rid2 = self.jq.enqueue("v2.mp4")
        self.jq.cancel(rid2)
        job2 = self.jq.get_job(rid2)
        assert job2.status == "cancelled"

    def test_get_job_by_id(self):
        rid = self.jq.enqueue("v.mp4", job_id="my-uuid")
        job = self.jq.get_job_by_id("my-uuid")
        assert job is not None
        assert job.id == rid

    def test_list_and_count(self):
        for i in range(3):
            self.jq.enqueue(f"v{i}.mp4")
        assert self.jq.count_jobs() == 3
        jobs = self.jq.list_jobs(limit=2)
        assert len(jobs) == 2

    def test_get_resumable_with_artifacts(self):
        rid = self.jq.enqueue("v.mp4", job_id="resume-test")
        job = self.jq.get_job(rid)

        # V0.3.1: 产物写入 artifacts/ 并创建 manifest
        artifacts_dir = os.path.join(job.job_dir, "artifacts")
        os.makedirs(artifacts_dir, exist_ok=True)

        # 标记 RESOLVING 完成
        with open(os.path.join(artifacts_dir, "audio.wav"), "w") as f:
            f.write("fake audio")
        self.jq.save_stage_manifest(rid, JobState.RESOLVING,
            StageManifest(stage="resolving", status="completed",
                          outputs=["audio.wav"], created_at="2026-01-01T00:00:00Z"))

        # 标记 TRANSCRIBING 完成
        with open(os.path.join(artifacts_dir, "transcript.json"), "w") as f:
            f.write('{"text": "hello"}')
        self.jq.save_stage_manifest(rid, JobState.TRANSCRIBING,
            StageManifest(stage="transcribing", status="completed",
                          outputs=["transcript.json"], created_at="2026-01-01T00:00:00Z"))

        # RESOLVING done, DOWNLOADING missing → next = DOWNLOADING
        stage = self.jq.get_resumable_stage(rid)
        assert stage == JobState.DOWNLOADING

    def test_job_dir_creation(self):
        rid = self.jq.enqueue("v.mp4", job_id="dir-test")
        job = self.jq.get_job(rid)
        assert "jobs" in job.job_dir
        assert "dir-test" in job.job_dir
        assert os.path.isdir(job.job_dir)

    def test_completed_workspace_is_removed(self):
        rid = self.jq.enqueue("v.mp4", job_id="completed-cleanup")
        workspace = self.jq.get_job(rid).job_dir

        self.jq.complete(rid)

        assert not os.path.exists(workspace)
        assert self.jq.get_job(rid).job_dir is None

    def test_orphan_cleanup_removes_legacy_non_uuid_workspace(self):
        legacy = os.path.join(get_legacy_jobs_root(), "resume-test")
        os.makedirs(legacy, exist_ok=True)

        assert self.jq.cleanup_orphans(min_age_hours=0, jobs_root=get_legacy_jobs_root()) == 1
        assert not os.path.exists(legacy)

    def test_default_db_path_uses_state_dir_and_migrates_legacy_db(self):
        output = os.path.join(self.tmp, "exports")
        legacy = os.path.join(output, ".note_index", "video_notes.db")
        os.makedirs(os.path.dirname(legacy), exist_ok=True)
        conn = sqlite3.connect(legacy)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE legacy_marker(value TEXT NOT NULL)")
        conn.execute("INSERT INTO legacy_marker(value) VALUES ('from-wal')")
        conn.commit()

        db_path = get_default_db_path(output)

        assert db_path == os.path.join(get_default_state_dir(), "video_notes.db")
        migrated = sqlite3.connect(db_path)
        try:
            value = migrated.execute("SELECT value FROM legacy_marker").fetchone()[0]
        finally:
            migrated.close()
            conn.close()
        assert value == "from-wal"

    def test_progress_callback(self):
        events = []
        def cb(stage, msg, pct):
            events.append((stage, msg, pct))

        jq2 = JobQueue(self.db, output_dir=self.tmp, on_progress=cb)
        rid = jq2.enqueue("v.mp4")
        jq2.update_stage(rid, JobState.TRANSCRIBING, "working...", 50)
        jq2.complete(rid)

        assert len(events) >= 3  # enqueue + update + complete
        assert events[0][0] == JobState.PENDING   # enqueue notification
        assert events[1][0] == JobState.TRANSCRIBING
        assert events[-1][0] == JobState.COMPLETED
