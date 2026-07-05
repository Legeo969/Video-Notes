"""Tests for NoteRepository."""
import os
import tempfile
import unittest

from src.infrastructure.db.gateway import DatabaseGateway
from src.infrastructure.db.repositories import NoteRepository


class TestNoteRepository(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(self.tmpdir, "test.db")
        self.db_path = db_path
        self.gateway = DatabaseGateway(db_path)
        self.gateway.initialize()
        self._ctx = self.gateway.connection()
        self.conn = self._ctx.__enter__()
        self.repo = NoteRepository(self.conn)

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── upsert ──────────────────────────────────────────────────

    def test_upsert_inserts_new(self):
        note_id = self.repo.upsert("test.md", "Test Title", "Test Content")
        self.assertIsInstance(note_id, int)
        # verify via raw conn (same connection — sees uncommitted data)
        row = self.conn.execute(
            "SELECT * FROM notes WHERE id=?", (note_id,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["rel_path"], "test.md")

    def test_upsert_updates_existing(self):
        self.repo.upsert("test.md", "Original Title")
        self.repo.upsert("test.md", "Updated Title")
        rows = self.conn.execute(
            "SELECT * FROM notes WHERE rel_path='test.md'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Updated Title")

    # ── get_by_id ───────────────────────────────────────────────

    def test_get_by_id_existing(self):
        note_id = self.repo.upsert("get_by_id.md", "Get By ID", "content")
        result = self.repo.get_by_id(note_id)
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "Get By ID")
        self.assertEqual(result["rel_path"], "get_by_id.md")
        self.assertIn("id", result)
        self.assertIn("created_at", result)
        self.assertIn("updated_at", result)

    def test_get_by_id_nonexistent(self):
        result = self.repo.get_by_id(9999)
        self.assertIsNone(result)

    # ── get_by_rel_path ─────────────────────────────────────────

    def test_get_by_rel_path_existing(self):
        self.repo.upsert("rel_path_test.md", "RelPath Test", "content")
        result = self.repo.get_by_rel_path("rel_path_test.md")
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "RelPath Test")

    def test_get_by_rel_path_nonexistent(self):
        result = self.repo.get_by_rel_path("nonexistent.md")
        self.assertIsNone(result)

    # ── search ──────────────────────────────────────────────────

    def test_search_returns_matching(self):
        self.repo.upsert("python.md", "Python Guide", "Learn Python")
        self.repo.upsert("java.md", "Java Guide", "Learn Java")
        self.repo.upsert("rust.md", "Rust Guide", "Learn Rust")
        results = self.repo.search("Python")
        self.assertGreaterEqual(len(results), 1)
        titles = {r["title"] for r in results}
        self.assertIn("Python Guide", titles)

    # ── delete ──────────────────────────────────────────────────

    def test_delete_existing(self):
        note_id = self.repo.upsert("delete_me.md", "Delete Me")
        self.repo.delete(note_id)
        self.assertIsNone(self.repo.get_by_id(note_id))

    def test_delete_nonexistent_no_error(self):
        # Should not raise
        self.repo.delete(9999)

    # ── keywords ────────────────────────────────────────────────

    def test_keywords_saved_with_note(self):
        note_id = self.repo.upsert(
            "keywords.md",
            "Keywords Test",
            "content",
            keywords=["python", "sqlite", "testing"],
        )
        rows = self.conn.execute(
            "SELECT keyword FROM note_keywords WHERE note_id=? ORDER BY keyword",
            (note_id,),
        ).fetchall()
        keywords = [r["keyword"] for r in rows]
        self.assertEqual(keywords, ["python", "sqlite", "testing"])

if __name__ == "__main__":
    unittest.main()
