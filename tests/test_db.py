from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import agentic_runtime


initialize_database = agentic_runtime.initialize_database


class DatabaseTests(unittest.TestCase):
    def test_initialize_database_creates_expected_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "runtime.db"

            connection = initialize_database(db_path)
            try:
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }

                self.assertIn("projects", table_names)
                self.assertIn("tasks", table_names)
                self.assertIn("change_requests", table_names)
                self.assertIn("task_costs", table_names)
                self.assertIn("task_required_artifacts", table_names)
                self.assertIn("task_produced_artifacts", table_names)
                self.assertNotIn("task_dependencies", table_names)
            finally:
                connection.close()

    def test_initialize_database_applies_required_pragmas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "runtime.db"

            connection = initialize_database(db_path)
            try:
                journal_mode = connection.execute("PRAGMA journal_mode;").fetchone()[0]
                foreign_keys = connection.execute("PRAGMA foreign_keys;").fetchone()[0]
                synchronous = connection.execute("PRAGMA synchronous;").fetchone()[0]

                self.assertEqual(journal_mode.lower(), "wal")
                self.assertEqual(foreign_keys, 1)
                self.assertEqual(synchronous, 1)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
