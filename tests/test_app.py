from __future__ import annotations

import json
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from typing import Any

try:
    TestClient: Any = import_module("fastapi.testclient").TestClient
except ModuleNotFoundError:  # pragma: no cover - dependency may be absent in minimal environments
    TestClient = None

app_module = __import__("agentic_runtime.app", fromlist=["create_app"])
db_module = __import__("agentic_runtime.db", fromlist=["initialize_database"])

create_app = app_module.create_app
initialize_database = db_module.initialize_database


@unittest.skipIf(TestClient is None, "fastapi is not installed")
class WebAppTests(unittest.TestCase):
    def test_dashboard_and_project_pages_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "runtime.db"
            connection = initialize_database(db_path)
            try:
                self._seed_runtime(connection)
            finally:
                connection.close()

            client = TestClient(create_app(str(db_path)))

            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Project One", response.text)

            project_response = client.get("/projects/proj_001")
            self.assertEqual(project_response.status_code, 200)
            self.assertIn("Task DAG", project_response.text)
            self.assertIn("Create product brief", project_response.text)

    def test_task_detail_and_graph_api_render_expected_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "runtime.db"
            connection = initialize_database(db_path)
            try:
                self._seed_runtime(connection)
            finally:
                connection.close()

            client = TestClient(create_app(str(db_path)))

            task_response = client.get("/tasks/task_design")
            self.assertEqual(task_response.status_code, 200)
            self.assertIn("Design system", task_response.text)
            self.assertIn("product.brief", task_response.text)

            graph_response = client.get("/api/projects/proj_001/graph")
            self.assertEqual(graph_response.status_code, 200)
            graph = graph_response.json()
            self.assertEqual(len(graph["nodes"]), 2)
            self.assertEqual(len(graph["edges"]), 1)
            self.assertEqual(graph["edges"][0]["source"], "task_brief")
            self.assertEqual(graph["edges"][0]["target"], "task_design")

    def test_healthcheck_endpoint_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "runtime.db"
            client = TestClient(create_app(str(db_path)))

            response = client.get("/healthz")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ok"})

    def _seed_runtime(self, connection) -> None:
        connection.execute(
            """
            INSERT INTO projects (
                project_id, slug, title, description, state, local_repo_path, default_branch,
                github_owner, github_repo, github_repo_url, github_repo_status, visibility,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "proj_001",
                "project-one",
                "Project One",
                "A demo project",
                "activated",
                "/srv/opendagent/project-one",
                "main",
                None,
                None,
                None,
                "not_created",
                "private",
                "2026-04-18T09:00:00Z",
                "2026-04-18T09:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO goals (
                goal_id, project_id, parent_goal_id, title, description, source_channel,
                source_thread_ref, state, priority, approval_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "goal_001",
                "proj_001",
                None,
                "Bootstrap app",
                None,
                "cli",
                None,
                "active",
                90,
                "human_for_external_actions",
                "2026-04-18T09:00:00Z",
                "2026-04-18T09:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO capabilities (
                capability_name, version, category, risk_level, requires_approval, enabled,
                definition_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "spec.product.refine",
                "1.0",
                "product",
                "low",
                0,
                1,
                "{}",
                "2026-04-18T09:00:00Z",
                "2026-04-18T09:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO capabilities (
                capability_name, version, category, risk_level, requires_approval, enabled,
                definition_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "design.system.webapp",
                "1.0",
                "design",
                "low",
                0,
                1,
                "{}",
                "2026-04-18T09:00:00Z",
                "2026-04-18T09:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, goal_id, project_id, parent_task_id, originating_change_request_id,
                capability_name, title, description, state, priority, branch_name,
                workspace_path, base_commit_sha, result_commit_sha, allowed_paths_json,
                model_pool_hint, max_tokens, max_cost_usd, retry_count, max_retries,
                lease_owner_worker_id, lease_expires_at, started_at, completed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task_brief",
                "goal_001",
                "proj_001",
                None,
                None,
                "spec.product.refine",
                "Create product brief",
                None,
                "done",
                90,
                None,
                None,
                None,
                None,
                "[]",
                None,
                None,
                None,
                0,
                2,
                None,
                None,
                None,
                "2026-04-18T10:00:00Z",
                "2026-04-18T09:10:00Z",
                "2026-04-18T10:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, goal_id, project_id, parent_task_id, originating_change_request_id,
                capability_name, title, description, state, priority, branch_name,
                workspace_path, base_commit_sha, result_commit_sha, allowed_paths_json,
                model_pool_hint, max_tokens, max_cost_usd, retry_count, max_retries,
                lease_owner_worker_id, lease_expires_at, started_at, completed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task_design",
                "goal_001",
                "proj_001",
                None,
                None,
                "design.system.webapp",
                "Design system",
                None,
                "queued",
                80,
                None,
                None,
                None,
                None,
                "[]",
                None,
                None,
                None,
                0,
                2,
                None,
                None,
                None,
                None,
                "2026-04-18T09:20:00Z",
                "2026-04-18T10:02:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO task_produced_artifacts (production_id, task_id, artifact_key, artifact_type, delivery_mode, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("prod_001", "task_brief", "product.brief", "document", "file", "2026-04-18T09:15:00Z"),
        )
        connection.execute(
            "INSERT INTO task_required_artifacts (requirement_id, task_id, artifact_key, required_status, created_at) VALUES (?, ?, ?, ?, ?)",
            ("req_001", "task_design", "product.brief", "active", "2026-04-18T09:20:00Z"),
        )
        connection.execute(
            "INSERT INTO artifacts (artifact_id, project_id, goal_id, artifact_key, type, status, version, produced_by_task_id, value_json, file_path, metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "artifact_001",
                "proj_001",
                "goal_001",
                "product.brief",
                "document",
                "active",
                1,
                "task_brief",
                None,
                "product/product_brief_v1.md",
                None,
                "2026-04-18T10:00:00Z",
                "2026-04-18T10:00:00Z",
            ),
        )
        connection.commit()


if __name__ == "__main__":
    unittest.main()
