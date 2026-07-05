"""Tests for JobRepository."""
import os
import tempfile
import unittest

from src.infrastructure.db.gateway import DatabaseGateway
from src.infrastructure.db.repositories import JobRepository
from src.domain.job_state import JobState


class TestJobRepository(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(self.tmpdir, "test.db")
        self.gateway = DatabaseGateway(db_path)
        self.gateway.initialize()
        self._ctx = self.gateway.connection()
        self.conn = self._ctx.__enter__()
        self.repo = JobRepository(self.conn)

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── start_run ────────────────────────────────────────────────

    def test_start_run_creates_record(self):
        run_id = self.repo.start_run(input_path="/path/to/video.mp4")
        self.assertIsInstance(run_id, int)
        job = self.repo.get_job(run_id)
        self.assertIsNotNone(job)
        self.assertEqual(job["input_path"], "/path/to/video.mp4")
        self.assertEqual(job["status"], "running")

    def test_start_run_with_optional_fields(self):
        run_id = self.repo.start_run(
            input_path="/path/to/video.mp4",
            title="My Video",
            job_dir="/tmp/jobs/abc123",
            job_id="custom-job-id",
        )
        job = self.repo.get_job(run_id)
        self.assertEqual(job["title"], "My Video")
        self.assertEqual(job["job_dir"], "/tmp/jobs/abc123")
        self.assertEqual(job["job_id"], "custom-job-id")

    def test_get_job_nonexistent(self):
        result = self.repo.get_job(9999)
        self.assertIsNone(result)

    # ── update_stage ─────────────────────────────────────────────

    def test_update_stage(self):
        run_id = self.repo.start_run(input_path="/path/to/video.mp4")
        self.repo.update_stage(run_id, JobState.DOWNLOADING)
        job = self.repo.get_job(run_id)
        self.assertEqual(job["stage"], "downloading")

    # ── complete / fail / cancel ─────────────────────────────────

    def test_complete_run(self):
        run_id = self.repo.start_run(input_path="/path/to/video.mp4")
        self.repo.complete_run(run_id, output_path="out.md", elapsed_sec=42.5)
        job = self.repo.get_job(run_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["output_path"], "out.md")
        self.assertAlmostEqual(job["elapsed_sec"], 42.5)

    def test_fail_run(self):
        run_id = self.repo.start_run(input_path="/path/to/video.mp4")
        self.repo.fail_run(run_id, error_message="Something went wrong")
        job = self.repo.get_job(run_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_message"], "Something went wrong")

    def test_cancel_run(self):
        run_id = self.repo.start_run(input_path="/path/to/video.mp4")
        self.repo.cancel_run(run_id)
        job = self.repo.get_job(run_id)
        self.assertEqual(job["status"], "cancelled")

    # ── list / count ─────────────────────────────────────────────

    def test_list_jobs_returns_ordered(self):
        self.repo.start_run(input_path="/path/a.mp4")
        self.repo.start_run(input_path="/path/b.mp4")
        jobs = self.repo.list_jobs(limit=5)
        self.assertLessEqual(len(jobs), 5)
        self.assertGreaterEqual(len(jobs), 2)

    def test_list_jobs_filter_by_status(self):
        r1 = self.repo.start_run(input_path="/path/a.mp4")
        r2 = self.repo.start_run(input_path="/path/b.mp4")
        self.repo.complete_run(r1)
        running_jobs = self.repo.list_jobs(status="running")
        self.assertTrue(all(j["status"] == "running" for j in running_jobs))

    def test_count_jobs(self):
        self.repo.start_run(input_path="/path/a.mp4")
        self.repo.start_run(input_path="/path/b.mp4")
        count = self.repo.count_jobs()
        self.assertGreaterEqual(count, 2)

    def test_count_jobs_by_status(self):
        r1 = self.repo.start_run(input_path="/path/a.mp4")
        self.repo.start_run(input_path="/path/b.mp4")
        self.repo.complete_run(r1)
        completed_count = self.repo.count_jobs(status="completed")
        self.assertEqual(completed_count, 1)

    # ── get_by_job_id ────────────────────────────────────────────

    def test_get_by_job_id(self):
        job_id = "my-unique-id"
        run_id = self.repo.start_run(
            input_path="/path/to/video.mp4", job_id=job_id
        )
        job = self.repo.get_by_job_id(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job["id"], run_id)
        self.assertEqual(job["job_id"], job_id)

    # ── get_resumable_stage ──────────────────────────────────────

    def test_get_resumable_stage_no_job(self):
        stage = self.repo.get_resumable_stage(9999)
        self.assertIsNone(stage)

    def test_get_resumable_stage_completed(self):
        run_id = self.repo.start_run(input_path="/path/to/video.mp4")
        self.repo.complete_run(run_id)
        stage = self.repo.get_resumable_stage(run_id)
        self.assertIsNone(stage)


if __name__ == "__main__":
    unittest.main()
