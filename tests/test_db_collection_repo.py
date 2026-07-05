"""Tests for CollectionRepository."""
import os
import sqlite3
import tempfile
import unittest

from src.application.collections.schema import initialize_collections
from src.infrastructure.db.gateway import DatabaseGateway
from src.infrastructure.db.repositories import CollectionRepository


class TestCollectionRepository(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(self.tmpdir, "test.db")
        self.gateway = DatabaseGateway(db_path)
        self.gateway.initialize()
        # Collection tables are NOT part of initialize_database — add them
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        initialize_collections(conn)
        conn.commit()
        conn.close()
        # Now open via gateway
        self._ctx = self.gateway.connection()
        self.conn = self._ctx.__enter__()
        # Re-apply collection schema on this connection too
        initialize_collections(self.conn)
        self.repo = CollectionRepository(self.conn)

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── insert_collection ────────────────────────────────────

    def test_insert_collection(self):
        self.repo.insert_collection(
            "my-course", "My Course", collection_type="course"
        )
        row = self.conn.execute(
            "SELECT collection_id, title, collection_type FROM collections"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["collection_id"], "my-course")
        self.assertEqual(row["title"], "My Course")
        self.assertEqual(row["collection_type"], "course")

    def test_insert_collection_duplicate(self):
        self.repo.insert_collection("same-id", "First")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.insert_collection("same-id", "Second")

    # ── list_collections ─────────────────────────────────────

    def test_list_collections(self):
        self.repo.insert_collection("b-slug", "B")
        self.repo.insert_collection("a-slug", "A")
        colls = self.repo.list_collections()
        self.assertEqual(len(colls), 2)
        # Ordered by created_at DESC — B first (inserted earlier)
        self.assertEqual(colls[0]["title"], "B")
        self.assertEqual(colls[1]["title"], "A")

    # ── get_collection_by_id ─────────────────────────────────

    def test_get_collection_by_id(self):
        self.repo.insert_collection("my-id", "My Course",
                                    description="desc", template_id="t1")
        coll = self.repo.get_collection_by_id("my-id")
        self.assertIsNotNone(coll)
        self.assertEqual(coll["title"], "My Course")
        self.assertEqual(coll["description"], "desc")
        self.assertEqual(coll["template_id"], "t1")

    def test_get_collection_by_id_nonexistent(self):
        result = self.repo.get_collection_by_id("no-such")
        self.assertIsNone(result)

    # ── insert_item / get_item ───────────────────────────────

    def test_insert_item(self):
        self.repo.insert_collection("c1", "Course")
        self.repo.insert_item("c1", "job-001", item_index=0,
                              title="Lesson 1", status="completed")
        item = self.repo.get_item("c1", "job-001")
        self.assertIsNotNone(item)
        self.assertEqual(item["job_id"], "job-001")
        self.assertEqual(item["item_index"], 0)
        self.assertEqual(item["title"], "Lesson 1")
        self.assertEqual(item["status"], "completed")

    # ── get_items ────────────────────────────────────────────

    def test_get_items(self):
        self.repo.insert_collection("c1", "Course")
        self.repo.insert_item("c1", "j1", item_index=1)
        self.repo.insert_item("c1", "j2", item_index=0)
        items = self.repo.get_items("c1")
        self.assertEqual(len(items), 2)
        # Ordered by item_index
        self.assertEqual(items[0]["item_index"], 0)
        self.assertEqual(items[1]["item_index"], 1)

    # ── update_item ──────────────────────────────────────────

    def test_update_item(self):
        self.repo.insert_collection("c1", "Course")
        self.repo.insert_item("c1", "job-001", item_index=0,
                              title="Original", status="pending")
        self.repo.update_item("c1", "job-001", title="Updated",
                              status="completed")
        item = self.repo.get_item("c1", "job-001")
        self.assertEqual(item["title"], "Updated")
        self.assertEqual(item["status"], "completed")

    # ── get_max_item_index ───────────────────────────────────

    def test_get_max_item_index(self):
        self.repo.insert_collection("c1", "Course")
        # No items yet
        self.assertEqual(self.repo.get_max_item_index("c1"), -1)
        self.repo.insert_item("c1", "j1", item_index=0)
        self.repo.insert_item("c1", "j2", item_index=5)
        self.assertEqual(self.repo.get_max_item_index("c1"), 5)

    # ── check_item_exists ────────────────────────────────────

    def test_check_item_exists(self):
        self.repo.insert_collection("c1", "Course")
        self.assertFalse(self.repo.check_item_exists("c1", "no-such"))
        self.repo.insert_item("c1", "j1", item_index=0)
        self.assertTrue(self.repo.check_item_exists("c1", "j1"))
        self.assertFalse(self.repo.check_item_exists("c1", "no-such"))


if __name__ == "__main__":
    unittest.main()
