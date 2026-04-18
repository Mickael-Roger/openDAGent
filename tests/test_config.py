from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import agentic_runtime


load_app_config = agentic_runtime.load_app_config
load_yaml_file = __import__("agentic_runtime.config", fromlist=["load_yaml_file"]).load_yaml_file


class ConfigTests(unittest.TestCase):
    def test_top_level_package_exports_public_api(self) -> None:
        self.assertIs(agentic_runtime.load_app_config, load_app_config)
        self.assertTrue(callable(agentic_runtime.initialize_database))

    def test_load_app_config_reads_expected_sections(self) -> None:
        config = load_app_config(Path("runtime/config/app.yaml"))

        self.assertEqual(config.runtime["db_path"], "runtime/runtime.db")
        self.assertEqual(config.runtime["workdir"], ".")
        self.assertTrue(config.server_enabled())
        self.assertEqual(config.server_host(), "127.0.0.1")
        self.assertEqual(config.server_port(), 8080)
        self.assertEqual(config.git["default_branch"], "main")
        self.assertEqual(config.scheduler["max_running_tasks_total"], 8)
        self.assertFalse(config.inputs["discord"]["enabled"])
        self.assertEqual(config.inputs["discord"]["allowed_guild_ids"], [])
        self.assertEqual(config.llm["default_provider"], "openai")

    def test_load_yaml_file_handles_model_provider_lists(self) -> None:
        config = load_yaml_file(Path("runtime/config/models.yaml"))

        self.assertEqual(config["providers"][0]["id"], "openai")
        self.assertEqual(config["providers"][2]["auth"]["type"], "none")
        self.assertTrue(config["models"][0]["capabilities"]["support_tools"])

    def test_load_yaml_file_rejects_non_mapping_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            invalid_yaml = Path(tmp_dir) / "invalid.yaml"
            invalid_yaml.write_text("- item\n- item2\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_yaml_file(invalid_yaml)

    def test_load_yaml_file_strips_inline_comments_from_scalars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / "comments.yaml"
            yaml_path.write_text(
                "runtime:\n  poll_interval_seconds: 5 # seconds\n"
                "items:\n  - value # comment\n",
                encoding="utf-8",
            )

            loaded = load_yaml_file(yaml_path)

            self.assertEqual(loaded["runtime"]["poll_interval_seconds"], 5)
            self.assertEqual(loaded["items"][0], "value")


if __name__ == "__main__":
    unittest.main()
