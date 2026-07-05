"""Tests for DatabaseGateway."""
import os
import tempfile
import sqlite3
import unittest

from src.infrastructure.db.gateway import DatabaseGateway


class TestDatabaseGateway(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.gateway = DatabaseGateway(self.db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_initialize_creates_tables(self):
        self.gateway.initialize()
        conn = sqlite3.connect(self.db_path)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = {r[0] for r in tables}
            for expected in ("notes", "processing_runs", "schema_migrations"):
                self.assertIn(expected, table_names)
        finally:
            conn.close()

    def test_connection_context_manager(self):
        self.gateway.initialize()
        with self.gateway.connection() as conn:
            self.assertIsInstance(conn, sqlite3.Connection)
            conn.execute("INSERT INTO notes (rel_path, title, content) VALUES ('t.md', 'T', 'C')")
        conn2 = sqlite3.connect(self.db_path)
        try:
            count = conn2.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn2.close()

    def test_connection_has_row_factory(self):
        self.gateway.initialize()
        with self.gateway.connection() as conn:
            self.assertIs(conn.row_factory, sqlite3.Row)

    def test_db_path_property(self):
        self.assertEqual(self.gateway.db_path, self.db_path)

    def test_connection_commits_on_exit(self):
        self.gateway.initialize()
        with self.gateway.connection() as conn:
            conn.execute(
                "INSERT INTO notes (rel_path, title, content) VALUES (?, ?, ?)",
                ("auto_commit.md", "Auto", "Committed"),
            )
        conn2 = sqlite3.connect(self.db_path)
        try:
            count = conn2.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            self.assertEqual(count, 1, "Connection should auto-commit on context exit")
        finally:
            conn2.close()

    def test_initialize_is_idempotent(self):
        self.gateway.initialize()
        self.gateway.initialize()
