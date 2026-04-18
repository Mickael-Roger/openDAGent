from __future__ import annotations

import unittest

ids_module = __import__("agentic_runtime.ids", fromlist=["new_id"])
models_module = __import__(
    "agentic_runtime.models",
    fromlist=["ArtifactRecord", "ExecutionContext", "ExecutionResult", "TaskRecord"],
)
time_module = __import__("agentic_runtime.time", fromlist=["utc_now", "utc_now_iso"])

new_id = ids_module.new_id
ArtifactRecord = models_module.ArtifactRecord
ExecutionContext = models_module.ExecutionContext
ExecutionResult = models_module.ExecutionResult
TaskRecord = models_module.TaskRecord
utc_now = time_module.utc_now
utc_now_iso = time_module.utc_now_iso


class CoreUtilityTests(unittest.TestCase):
    def test_new_id_normalizes_prefix(self) -> None:
        generated = new_id(" Task ")

        self.assertTrue(generated.startswith("task_"))
        self.assertEqual(len(generated.split("_", 1)[1]), 12)

    def test_new_id_rejects_empty_prefix(self) -> None:
        with self.assertRaises(ValueError):
            new_id("   ")

    def test_utc_now_returns_utc_datetime(self) -> None:
        timestamp = utc_now()

        self.assertIsNotNone(timestamp.tzinfo)
        self.assertEqual(timestamp.utcoffset().total_seconds(), 0)

    def test_utc_now_iso_uses_z_suffix(self) -> None:
        iso_value = utc_now_iso()

        self.assertTrue(iso_value.endswith("Z"))
        self.assertIn("T", iso_value)

    def test_task_record_defaults_use_empty_lists(self) -> None:
        record = TaskRecord(
            task_id="task_001",
            goal_id="goal_001",
            project_id="proj_001",
            capability_name="spec.product.refine",
            state="created",
            priority=50,
        )

        self.assertEqual(record.allowed_paths, [])
        self.assertEqual(record.required_artifacts, [])
        self.assertEqual(record.produced_artifacts, [])

    def test_execution_models_store_expected_fields(self) -> None:
        context = ExecutionContext(
            db=object(),
            repo_path="/repo",
            workspace_path="/workspace",
            project_id="proj_001",
            goal_id="goal_001",
            worker_id="worker_001",
            model_pool="balanced",
            now_iso="2026-04-18T10:14:00Z",
        )
        result = ExecutionResult(
            changed_files=["product/product_brief_v1.md"],
            output_artifacts=[
                ArtifactRecord(
                    artifact_id="artifact_001",
                    artifact_key="product.brief",
                    artifact_type="document",
                    status="active",
                    version=1,
                    produced_by_task_id="task_001",
                    file_path="product/product_brief_v1.md",
                )
            ],
            summary="Created product brief",
        )

        self.assertEqual(context.model_pool, "balanced")
        self.assertEqual(result.prompt_tokens, 0)
        self.assertEqual(result.estimated_cost_usd, 0.0)
        self.assertEqual(result.output_artifacts[0].artifact_key, "product.brief")


if __name__ == "__main__":
    unittest.main()
