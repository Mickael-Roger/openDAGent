from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

cli_module = __import__("agentic_runtime.cli", fromlist=["main"])


class CliTests(unittest.TestCase):
    def test_main_can_write_default_config_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.yaml"

            exit_code = cli_module.main([
                "--init-config",
                str(config_path),
            ])

            self.assertEqual(exit_code, 0)
            self.assertTrue(config_path.exists())
            self.assertIn("server:", config_path.read_text(encoding="utf-8"))

    def test_main_initializes_database_and_exits_when_web_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.yaml"
            config_path.write_text(
                "runtime:\n"
                "  workdir: .\n"
                "  db_path: runtime/runtime.db\n"
                "server:\n"
                "  enabled: false\n",
                encoding="utf-8",
            )

            with patch.object(cli_module, "_run_server") as run_server:
                exit_code = cli_module.main([
                    "--config",
                    str(config_path),
                    "--workdir",
                    tmp_dir,
                ])

            self.assertEqual(exit_code, 0)
            self.assertFalse(run_server.called)
            self.assertTrue((Path(tmp_dir) / "runtime" / "runtime.db").exists())

    def test_main_uses_cli_overrides_for_server_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.yaml"
            config_path.write_text(
                "runtime:\n"
                "  workdir: .\n"
                "  db_path: runtime/runtime.db\n"
                "server:\n"
                "  enabled: false\n"
                "  host: 127.0.0.1\n"
                "  port: 8080\n",
                encoding="utf-8",
            )

            with patch.object(cli_module, "_run_server") as run_server:
                exit_code = cli_module.main([
                    "--config",
                    str(config_path),
                    "--workdir",
                    tmp_dir,
                    "--web",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9090",
                ])

            self.assertEqual(exit_code, 0)
            run_server.assert_called_once()
            _, kwargs = run_server.call_args
            self.assertEqual(
                str(kwargs["db_path"]),
                str((Path(tmp_dir) / "runtime" / "runtime.db").resolve()),
            )
            self.assertEqual(kwargs["host"], "0.0.0.0")
            self.assertEqual(kwargs["port"], 9090)


if __name__ == "__main__":
    unittest.main()
