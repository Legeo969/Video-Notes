"""Tests for ProvenanceRepository."""
import os
import sqlite3
import tempfile
import unittest

from src.infrastructure.db.gateway import DatabaseGateway
from src.infrastructure.db.repositories import ProvenanceRepository


class TestProvenanceRepository(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(self.tmpdir, "test.db")
        self.gateway = DatabaseGateway(db_path)
        self.gateway.initialize()
        self._ctx = self.gateway.connection()
        self.conn = self._ctx.__enter__()
        self.repo = ProvenanceRepository(self.conn)

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── helpers ────────────────────────────────────────────────

    def _insert_block(self, **overrides) -> int:
        """Insert a knowledge block row and return its id."""
        vals = dict(
            note_id="test/note.md",
            block_type="concept",
            title="Test Block",
            content="Test content",
            source_timestamp=None,
            source_text="",
            job_id="job-001",
            block_index=0,
        )
        vals.update(overrides)
        cur = self.conn.execute(
            """INSERT INTO knowledge_blocks
               (note_id, block_type, title, content,
                source_timestamp, source_text, job_id, block_index)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (vals["note_id"], vals["block_type"], vals["title"],
             vals["content"], vals["source_timestamp"],
             vals["source_text"], vals["job_id"], vals["block_index"]),
        )
        return cur.lastrowid

    def _count(self, table: str, where: str = "") -> int:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return self.conn.execute(sql).fetchone()[0]

    # ── clear_job ──────────────────────────────────────────────

    def test_clear_job_removes_all(self):
        self.repo.index_video_source("job-clear", "url", "https://example.com/v")
        self.assertEqual(self._count("video_sources", "job_id='job-clear'"), 1)

        self.repo.clear_job("job-clear")
        self.assertEqual(self._count("video_sources", "job_id='job-clear'"), 0)

    def test_clear_job_idempotent(self):
        """Clearing a non-existent job does not error."""
        self.repo.clear_job("nonexistent")

    # ── index_video_source ─────────────────────────────────────

    def test_index_video_source(self):
        self.repo.index_video_source(
            "job-vs", "url", "https://example.com/video.mp4",
            title="Test Video", duration=120.5,
            local_video_path="/tmp/video.mp4",
        )
        rows = self.conn.execute(
            "SELECT * FROM video_sources WHERE job_id='job-vs'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        r = dict(rows[0])
        self.assertEqual(r["job_id"], "job-vs")
        self.assertEqual(r["source_type"], "url")
        self.assertEqual(r["source_uri"], "https://example.com/video.mp4")
        self.assertEqual(r["title"], "Test Video")
        self.assertEqual(r["duration"], 120.5)
        self.assertEqual(r["local_video_path"], "/tmp/video.mp4")

    def test_index_video_source_replace(self):
        self.repo.index_video_source("job-replace", "url", "https://example.com/v1")
        self.repo.index_video_source("job-replace", "local", "/data/v2.mp4")
        rows = self.conn.execute(
            "SELECT * FROM video_sources WHERE job_id='job-replace'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_uri"], "/data/v2.mp4")

    # ── index_transcript_segments ──────────────────────────────

    def test_index_transcript_segments(self):
        segments = [
            {"segment_index": 0, "start_time": 0.0, "end_time": 5.0, "text": "Hello"},
            {"segment_index": 1, "start_time": 5.0, "end_time": 10.0, "text": "World"},
            {"segment_index": 2, "start_time": 10.0, "end_time": 15.0, "text": "Foo"},
        ]
        cnt = self.repo.index_transcript_segments("job-ts", segments)
        self.assertEqual(cnt, 3)
        self.assertEqual(self._count("transcript_segments", "job_id='job-ts'"), 3)

    def test_index_transcript_segments_replace(self):
        segments = [{"segment_index": 0, "start_time": 0.0, "end_time": 1.0, "text": "A"}]
        self.repo.index_transcript_segments("job-ts2", segments)
        # Re-insert same segment_index with different text
        segments2 = [{"segment_index": 0, "start_time": 0.0, "end_time": 2.0, "text": "B"}]
        self.repo.index_transcript_segments("job-ts2", segments2)
        rows = self.conn.execute(
            "SELECT text, end_time FROM transcript_segments WHERE job_id='job-ts2'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "B")
        self.assertEqual(rows[0]["end_time"], 2.0)

    # ── index_frame_assets ─────────────────────────────────────

    def test_index_frame_assets(self):
        frames = [
            {"frame_index": 0, "timestamp": 5.0, "path": "/frames/001.jpg",
             "perceptual_hash": "abc"},
            {"frame_index": 1, "timestamp": 10.0, "path": "/frames/002.jpg"},
        ]
        cnt = self.repo.index_frame_assets("job-fa", frames)
        self.assertEqual(cnt, 2)
        self.assertEqual(self._count("frame_assets", "job_id='job-fa'"), 2)
        row = self.conn.execute(
            "SELECT * FROM frame_assets WHERE job_id='job-fa' AND frame_index=0"
        ).fetchone()
        self.assertEqual(row["perceptual_hash"], "abc")
        self.assertEqual(row["path"], "/frames/001.jpg")

    def test_index_frame_assets_replace(self):
        frames = [{"frame_index": 0, "timestamp": 5.0, "path": "/old.jpg"}]
        self.repo.index_frame_assets("job-fa2", frames)
        frames2 = [{"frame_index": 0, "timestamp": 6.0, "path": "/new.jpg"}]
        self.repo.index_frame_assets("job-fa2", frames2)
        rows = self.conn.execute(
            "SELECT * FROM frame_assets WHERE job_id='job-fa2'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], "/new.jpg")

    # ── link_block_sources ─────────────────────────────────────

    def test_link_block_sources(self):
        block_id = self._insert_block(job_id="job-bs")
        sources = [
            {"block_id": block_id, "source_kind": "transcript",
             "source_id": 1, "relevance": 0.9, "quote": "Hello world"},
            {"block_id": block_id, "source_kind": "frame",
             "source_id": 5, "relevance": 0.8},
        ]
        self.repo.link_block_sources(sources)
        rows = self.conn.execute(
            "SELECT * FROM block_sources WHERE block_id=? ORDER BY source_kind",
            (block_id,),
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source_kind"], "frame")
        self.assertEqual(rows[1]["source_kind"], "transcript")
        self.assertEqual(rows[1]["quote"], "Hello world")

    def test_link_block_sources_ignore_duplicate(self):
        block_id = self._insert_block(job_id="job-bs2")
        src = {"block_id": block_id, "source_kind": "transcript", "source_id": 1}
        self.repo.link_block_sources([src])
        self.repo.link_block_sources([src])  # duplicate — should be ignored
        rows = self.conn.execute(
            "SELECT * FROM block_sources WHERE block_id=?", (block_id,)
        ).fetchall()
        self.assertEqual(len(rows), 1)

    # ── load_blocks_for_job ────────────────────────────────────

    def test_load_blocks_for_job(self):
        b1 = self._insert_block(job_id="job-lb", block_index=0, title="Block A")
        b2 = self._insert_block(job_id="job-lb", block_index=1, title="Block B")
        self._insert_block(job_id="other-job", block_index=0, title="Other")

        blocks = self.repo.load_blocks_for_job("job-lb")
        self.assertEqual(len(blocks), 2)
        titles = [b["title"] for b in blocks]
        self.assertIn("Block A", titles)
        self.assertIn("Block B", titles)

    def test_load_blocks_for_job_empty(self):
        blocks = self.repo.load_blocks_for_job("nonexistent")
        self.assertEqual(blocks, [])

    # ── load_transcript_segments ───────────────────────────────

    def test_load_transcript_segments(self):
        segs = [
            {"segment_index": 2, "start_time": 10.0, "end_time": 15.0, "text": "C"},
            {"segment_index": 0, "start_time": 0.0, "end_time": 5.0, "text": "A"},
            {"segment_index": 1, "start_time": 5.0, "end_time": 10.0, "text": "B"},
        ]
        self.repo.index_transcript_segments("job-ldts", segs)
        loaded = self.repo.load_transcript_segments("job-ldts")
        self.assertEqual(len(loaded), 3)
        # Must be ordered by segment_index
        texts = [r["text"] for r in loaded]
        self.assertEqual(texts, ["A", "B", "C"])

    # ── load_frame_assets ──────────────────────────────────────

    def test_load_frame_assets(self):
        frames = [
            {"frame_index": 3, "timestamp": 30.0, "path": "/f3.jpg"},
            {"frame_index": 0, "timestamp": 0.0, "path": "/f0.jpg"},
            {"frame_index": 1, "timestamp": 10.0, "path": "/f1.jpg"},
        ]
        self.repo.index_frame_assets("job-ldfa", frames)
        loaded = self.repo.load_frame_assets("job-ldfa")
        self.assertEqual(len(loaded), 3)
        # Must be ordered by frame_index
        paths = [r["path"] for r in loaded]
        self.assertEqual(paths, ["/f0.jpg", "/f1.jpg", "/f3.jpg"])

    # ── check_provenance_status ───────────────────────────────

    def test_check_provenance_status(self):
        self.repo.index_video_source("job-st", "url", "https://example.com/v")
        self.repo.index_transcript_segments("job-st", [
            {"segment_index": 0, "start_time": 0.0, "end_time": 5.0, "text": "A"},
            {"segment_index": 1, "start_time": 5.0, "end_time": 10.0, "text": "B"},
        ])
        self.repo.index_frame_assets("job-st", [
            {"frame_index": 0, "timestamp": 5.0, "path": "/f.jpg"},
        ])
        block_id = self._insert_block(job_id="job-st")
        self.repo.link_block_sources([
            {"block_id": block_id, "source_kind": "transcript", "source_id": 1},
        ])

        status = self.repo.check_provenance_status("job-st")
        self.assertEqual(status["video_sources"], 1)
        self.assertEqual(status["transcript_segments"], 2)
        self.assertEqual(status["frame_assets"], 1)
        self.assertEqual(status["block_sources"], 1)
        self.assertEqual(status["ocr_results"], 0)

    def test_check_provenance_status_no_data(self):
        status = self.repo.check_provenance_status("nonexistent")
        self.assertEqual(status["video_sources"], 0)
        self.assertEqual(status["transcript_segments"], 0)
        self.assertEqual(status["frame_assets"], 0)
        self.assertEqual(status["block_sources"], 0)
        self.assertEqual(status["ocr_results"], 0)

    # ── check_all_jobs_provenance ─────────────────────────────

    def test_check_all_jobs_provenance(self):
        # Job 1: full data
        self.repo.index_video_source("job-a", "url", "https://a.com/v",
                                     title="Video A")
        self.repo.index_transcript_segments("job-a", [
            {"segment_index": 0, "start_time": 0.0, "end_time": 5.0, "text": "A1"},
            {"segment_index": 1, "start_time": 5.0, "end_time": 10.0, "text": "A2"},
        ])
        self.repo.index_frame_assets("job-a", [
            {"frame_index": 0, "timestamp": 5.0, "path": "/a.jpg"},
        ])
        b_a = self._insert_block(job_id="job-a")
        self.repo.link_block_sources([
            {"block_id": b_a, "source_kind": "transcript", "source_id": 1},
        ])

        # Job 2: minimal data
        self.repo.index_video_source("job-b", "local", "/data/b.mp4",
                                     title="Video B")
        self.repo.index_transcript_segments("job-b", [
            {"segment_index": 0, "start_time": 0.0, "end_time": 3.0, "text": "B1"},
        ])
        # no frames, no blocks for job-b

        summary = self.repo.check_all_jobs_provenance()
        self.assertEqual(len(summary), 2)

        by_job = {r["job_id"]: r for r in summary}
        self.assertEqual(by_job["job-a"]["source_type"], "url")
        self.assertEqual(by_job["job-a"]["title"], "Video A")
        self.assertEqual(by_job["job-a"]["segments_count"], 2)
        self.assertEqual(by_job["job-a"]["frames_count"], 1)
        self.assertEqual(by_job["job-a"]["sources_count"], 1)

        self.assertEqual(by_job["job-b"]["source_type"], "local")
        self.assertEqual(by_job["job-b"]["title"], "Video B")
        self.assertEqual(by_job["job-b"]["segments_count"], 1)
        self.assertEqual(by_job["job-b"]["frames_count"], 0)
        self.assertEqual(by_job["job-b"]["sources_count"], 0)


if __name__ == "__main__":
    unittest.main()
