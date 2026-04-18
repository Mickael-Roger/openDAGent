from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

artifacts_module = __import__(
    "agentic_runtime.artifacts",
    fromlist=[
        "hydrate_task_record",
        "is_task_executable",
        "register_task_output_artifacts",
        "resolve_latest_artifact",
    ],
)
db_module = __import__("agentic_runtime.db", fromlist=["initialize_database"])
models_module = __import__(
    "agentic_runtime.models",
    fromlist=["ArtifactRecord", "TaskRecord"],
)
scheduler_module = __import__("agentic_runtime.scheduler", fromlist=["queue_ready_tasks"])

hydrate_task_record = artifacts_module.hydrate_task_record
initialize_database = db_module.initialize_database
is_task_executable = artifacts_module.is_task_executable
queue_ready_tasks = scheduler_module.queue_ready_tasks
register_task_output_artifacts = artifacts_module.register_task_output_artifacts
resolve_latest_artifact = artifacts_module.resolve_latest_artifact
ArtifactRecord = models_module.ArtifactRecord
TaskRecord = models_module.TaskRecord


class ArtifactOrchestrationTests(unittest.TestCase):
    def test_resolve_latest_artifact_returns_latest_valid_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.db")
            try:
                self._insert_project_graph(connection)
                self._insert_task(connection, task_id="task_001", state="done")
                self._insert_task(connection, task_id="task_002", state="done")
                self._insert_task(connection, task_id="task_003", state="failed")
                connection.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, project_id, goal_id, artifact_key, type, status, version,
                        produced_by_task_id, value_json, file_path, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "artifact_old",
                        "proj_001",
                        "goal_001",
                        "domain.name",
                        "decision",
                        "active",
                        1,
                        "task_001",
                        json.dumps({"domain": "old.example.com"}),
                        None,
                        None,
                        "2026-04-18T10:00:00Z",
                        "2026-04-18T10:00:00Z",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, project_id, goal_id, artifact_key, type, status, version,
                        produced_by_task_id, value_json, file_path, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "artifact_new",
                        "proj_001",
                        "goal_001",
                        "domain.name",
                        "decision",
                        "approved",
                        2,
                        "task_002",
                        json.dumps({"domain": "new.example.com"}),
                        None,
                        None,
                        "2026-04-18T11:00:00Z",
                        "2026-04-18T11:00:00Z",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, project_id, goal_id, artifact_key, type, status, version,
                        produced_by_task_id, value_json, file_path, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "artifact_rejected",
                        "proj_001",
                        "goal_001",
                        "domain.name",
                        "decision",
                        "rejected",
                        3,
                        "task_003",
                        json.dumps({"domain": "bad.example.com"}),
                        None,
                        None,
                        "2026-04-18T12:00:00Z",
                        "2026-04-18T12:00:00Z",
                    ),
                )
                connection.commit()

                artifact = resolve_latest_artifact(connection, "proj_001", "domain.name")

                self.assertIsNotNone(artifact)
                self.assertEqual(artifact.version, 2)
                self.assertEqual(artifact.value_json["domain"], "new.example.com")
            finally:
                connection.close()

    def test_task_executability_depends_on_required_artifact_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.db")
            try:
                self._insert_project_graph(connection)
                self._insert_task(connection, task_id="task_001", state="done")
                self._insert_task(connection, task_id="task_consumer", state="created")
                connection.execute(
                    """
                    INSERT INTO task_required_artifacts (
                        requirement_id, task_id, artifact_key, required_status, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "req_001",
                        "task_consumer",
                        "domain.name",
                        "approved",
                        "2026-04-18T10:00:00Z",
                    ),
                )
                connection.commit()

                self.assertFalse(is_task_executable(connection, "task_consumer"))

                connection.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, project_id, goal_id, artifact_key, type, status, version,
                        produced_by_task_id, value_json, file_path, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "artifact_approved",
                        "proj_001",
                        "goal_001",
                        "domain.name",
                        "decision",
                        "approved",
                        1,
                        "task_001",
                        json.dumps({"domain": "example.com"}),
                        None,
                        None,
                        "2026-04-18T11:00:00Z",
                        "2026-04-18T11:00:00Z",
                    ),
                )
                connection.commit()

                self.assertTrue(is_task_executable(connection, "task_consumer"))
            finally:
                connection.close()

    def test_task_executability_is_scoped_to_goal_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.db")
            try:
                self._insert_project_graph(connection)
                self._insert_second_goal(connection)
                self._insert_task(connection, task_id="task_goal_one", state="done")
                self._insert_task(
                    connection,
                    task_id="task_goal_two_consumer",
                    state="created",
                    goal_id="goal_002",
                )
                connection.execute(
                    """
                    INSERT INTO task_required_artifacts (
                        requirement_id, task_id, artifact_key, required_status, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "req_goal_scope",
                        "task_goal_two_consumer",
                        "domain.name",
                        "approved",
                        "2026-04-18T10:00:00Z",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, project_id, goal_id, artifact_key, type, status, version,
                        produced_by_task_id, value_json, file_path, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "artifact_goal_one",
                        "proj_001",
                        "goal_001",
                        "domain.name",
                        "decision",
                        "approved",
                        1,
                        "task_goal_one",
                        json.dumps({"domain": "goal-one.example.com"}),
                        None,
                        None,
                        "2026-04-18T11:00:00Z",
                        "2026-04-18T11:00:00Z",
                    ),
                )
                connection.commit()

                self.assertFalse(is_task_executable(connection, "task_goal_two_consumer"))
            finally:
                connection.close()

    def test_queue_ready_tasks_uses_artifact_availability_not_task_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.db")
            try:
                self._insert_project_graph(connection)
                self._insert_task(connection, task_id="task_source", state="failed")
                self._insert_task(connection, task_id="task_waiting", state="created")
                connection.execute(
                    """
                    INSERT INTO task_required_artifacts (
                        requirement_id, task_id, artifact_key, required_status, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "req_queue",
                        "task_waiting",
                        "domain.name",
                        "approved",
                        "2026-04-18T10:00:00Z",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, project_id, goal_id, artifact_key, type, status, version,
                        produced_by_task_id, value_json, file_path, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "artifact_queue",
                        "proj_001",
                        "goal_001",
                        "domain.name",
                        "decision",
                        "approved",
                        1,
                        "task_source",
                        json.dumps({"domain": "queue.example.com"}),
                        None,
                        None,
                        "2026-04-18T11:00:00Z",
                        "2026-04-18T11:00:00Z",
                    ),
                )
                connection.commit()

                queued = queue_ready_tasks(connection, now_iso="2026-04-18T11:05:00Z")
                task_state = connection.execute(
                    "SELECT state FROM tasks WHERE task_id = ?",
                    ("task_waiting",),
                ).fetchone()[0]

                self.assertEqual(queued, ["task_waiting"])
                self.assertEqual(task_state, "queued")
            finally:
                connection.close()

    def test_queue_ready_tasks_skips_non_runnable_goal_and_project_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.db")
            try:
                self._insert_project_graph(connection)
                self._insert_task(connection, task_id="task_paused_goal", state="created")
                connection.execute(
                    "UPDATE goals SET state = 'paused' WHERE goal_id = ?",
                    ("goal_001",),
                )
                connection.commit()

                queued = queue_ready_tasks(connection, now_iso="2026-04-18T11:05:00Z")
                self.assertEqual(queued, [])

                connection.execute(
                    "UPDATE goals SET state = 'active' WHERE goal_id = ?",
                    ("goal_001",),
                )
                connection.execute(
                    "UPDATE projects SET state = 'paused' WHERE project_id = ?",
                    ("proj_001",),
                )
                connection.commit()

                queued = queue_ready_tasks(connection, now_iso="2026-04-18T11:10:00Z")
                self.assertEqual(queued, [])
            finally:
                connection.close()

    def test_register_task_output_artifacts_versions_structured_and_file_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.db")
            try:
                self._insert_project_graph(connection)
                self._insert_task(connection, task_id="task_producer", state="running")
                task = TaskRecord(
                    task_id="task_producer",
                    goal_id="goal_001",
                    project_id="proj_001",
                    capability_name="spec.product.refine",
                    state="done",
                    priority=50,
                    produced_artifacts=[
                        models_module.ProducedArtifact(
                            artifact_key="domain.name",
                            artifact_type="decision",
                            delivery_mode="value",
                        ),
                        models_module.ProducedArtifact(
                            artifact_key="product.brief",
                            artifact_type="document",
                            delivery_mode="file",
                        ),
                    ],
                )

                persisted = register_task_output_artifacts(
                    connection,
                    task,
                    [
                        ArtifactRecord(
                            artifact_id="",
                            artifact_key="domain.name",
                            artifact_type="decision",
                            status="approved",
                            version=0,
                            produced_by_task_id=None,
                            value_json={"domain": "example.com"},
                        ),
                        ArtifactRecord(
                            artifact_id="",
                            artifact_key="product.brief",
                            artifact_type="document",
                            status="active",
                            version=0,
                            produced_by_task_id=None,
                            file_path="product/product_brief_v1.md",
                        ),
                    ],
                    now_iso="2026-04-18T11:30:00Z",
                )
                persisted_again = register_task_output_artifacts(
                    connection,
                    task,
                    [
                        ArtifactRecord(
                            artifact_id="",
                            artifact_key="domain.name",
                            artifact_type="decision",
                            status="approved",
                            version=0,
                            produced_by_task_id=None,
                            value_json={"domain": "example.org"},
                        )
                    ],
                    now_iso="2026-04-18T12:00:00Z",
                )
                connection.commit()

                self.assertEqual(persisted[0].version, 1)
                self.assertEqual(persisted[1].version, 1)
                self.assertEqual(persisted_again[0].version, 2)

                resolved = resolve_latest_artifact(connection, "proj_001", "domain.name")
                self.assertEqual(resolved.value_json["domain"], "example.org")
            finally:
                connection.close()

    def test_register_task_output_artifacts_rejects_undeclared_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.db")
            try:
                self._insert_project_graph(connection)
                self._insert_task(connection, task_id="task_contract", state="running")
                task = TaskRecord(
                    task_id="task_contract",
                    goal_id="goal_001",
                    project_id="proj_001",
                    capability_name="spec.product.refine",
                    state="done",
                    priority=50,
                    produced_artifacts=[
                        models_module.ProducedArtifact(
                            artifact_key="declared.key",
                            artifact_type="decision",
                            delivery_mode="value",
                        )
                    ],
                )

                with self.assertRaises(ValueError):
                    register_task_output_artifacts(
                        connection,
                        task,
                        [
                            ArtifactRecord(
                                artifact_id="",
                                artifact_key="undeclared.key",
                                artifact_type="decision",
                                status="approved",
                                version=0,
                                produced_by_task_id=None,
                                value_json={"value": "nope"},
                            )
                        ],
                        now_iso="2026-04-18T12:30:00Z",
                    )
            finally:
                connection.close()

    def test_register_task_output_artifacts_rolls_back_partial_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.db")
            try:
                self._insert_project_graph(connection)
                self._insert_task(connection, task_id="task_atomic", state="running")
                task = TaskRecord(
                    task_id="task_atomic",
                    goal_id="goal_001",
                    project_id="proj_001",
                    capability_name="spec.product.refine",
                    state="done",
                    priority=50,
                    produced_artifacts=[
                        models_module.ProducedArtifact(
                            artifact_key="good.key",
                            artifact_type="decision",
                            delivery_mode="value",
                        ),
                        models_module.ProducedArtifact(
                            artifact_key="bad.key",
                            artifact_type="decision",
                            delivery_mode="value",
                        ),
                    ],
                )

                with self.assertRaises(ValueError):
                    register_task_output_artifacts(
                        connection,
                        task,
                        [
                            ArtifactRecord(
                                artifact_id="",
                                artifact_key="good.key",
                                artifact_type="decision",
                                status="approved",
                                version=0,
                                produced_by_task_id=None,
                                value_json={"value": "ok"},
                            ),
                            ArtifactRecord(
                                artifact_id="",
                                artifact_key="bad.key",
                                artifact_type="decision",
                                status="approved",
                                version=0,
                                produced_by_task_id=None,
                                value_json={"value": "bad"},
                                file_path="bad.txt",
                            ),
                        ],
                        now_iso="2026-04-18T12:40:00Z",
                    )

                artifact_count = connection.execute(
                    "SELECT COUNT(*) FROM artifacts WHERE project_id = ?",
                    ("proj_001",),
                ).fetchone()[0]
                self.assertEqual(artifact_count, 0)
            finally:
                connection.close()

    def test_hydrate_task_record_loads_artifact_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.db")
            try:
                self._insert_project_graph(connection)
                self._insert_task(connection, task_id="task_hydrate", state="created")
                connection.execute(
                    """
                    INSERT INTO task_required_artifacts (
                        requirement_id, task_id, artifact_key, required_status, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("req_002", "task_hydrate", "product.brief", "active", "2026-04-18T10:00:00Z"),
                )
                connection.execute(
                    """
                    INSERT INTO task_produced_artifacts (
                        production_id, task_id, artifact_key, artifact_type, delivery_mode, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "prod_001",
                        "task_hydrate",
                        "architecture.design",
                        "document",
                        "file",
                        "2026-04-18T10:00:00Z",
                    ),
                )
                connection.commit()

                task = hydrate_task_record(connection, "task_hydrate")

                self.assertEqual(task.required_artifacts[0].artifact_key, "product.brief")
                self.assertEqual(task.produced_artifacts[0].delivery_mode, "file")
            finally:
                connection.close()

    def _insert_project_graph(self, connection) -> None:
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
                None,
                "activated",
                "/tmp/project-one",
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
                "Goal One",
                None,
                "cli",
                None,
                "active",
                50,
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
        connection.commit()

    def _insert_second_goal(self, connection) -> None:
        connection.execute(
            """
            INSERT INTO goals (
                goal_id, project_id, parent_goal_id, title, description, source_channel,
                source_thread_ref, state, priority, approval_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "goal_002",
                "proj_001",
                None,
                "Goal Two",
                None,
                "cli",
                None,
                "active",
                50,
                "human_for_external_actions",
                "2026-04-18T09:30:00Z",
                "2026-04-18T09:30:00Z",
            ),
        )
        connection.commit()

    def _insert_task(
        self,
        connection,
        task_id: str,
        state: str,
        goal_id: str = "goal_001",
    ) -> None:
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
                task_id,
                goal_id,
                "proj_001",
                None,
                None,
                "spec.product.refine",
                f"Title for {task_id}",
                None,
                state,
                50,
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
                "2026-04-18T10:00:00Z",
                "2026-04-18T10:00:00Z",
            ),
        )
        connection.commit()


if __name__ == "__main__":
    unittest.main()
