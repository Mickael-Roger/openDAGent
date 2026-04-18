from __future__ import annotations

import argparse
from importlib import import_module
from pathlib import Path

from .capabilities import register_builtins
from .config import AppConfig, load_app_config
from .db import initialize_database


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "opendagent" / "config.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openDAGent")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="YAML configuration file path",
    )
    parser.add_argument(
        "--init-config",
        help="Write the bundled default config to the given path and exit",
    )
    parser.add_argument("--host", help="Override web bind host")
    parser.add_argument("--port", type=int, help="Override web bind port")
    parser.add_argument("--db", help="Override SQLite database path")
    parser.add_argument("--workdir", help="Override runtime working directory")
    parser.add_argument(
        "--web",
        dest="web_enabled",
        action="store_true",
        help="Start the web interface",
    )
    parser.add_argument(
        "--no-web",
        dest="web_enabled",
        action="store_false",
        help="Start without the web interface",
    )
    parser.add_argument(
        "--init-db-only",
        action="store_true",
        help="Initialize the runtime database and exit",
    )
    parser.set_defaults(web_enabled=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.init_config:
        _write_default_config(Path(args.init_config))
        return 0

    config_path = Path(args.config).expanduser().resolve()
    if not config_path.exists():
        parser.error(
            "Configuration file not found. Create one with "
            "'openDAGent --init-config <path>' or pass --config."
        )

    config = load_app_config(config_path)

    workdir = _effective_workdir(config, args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    db_path = _effective_db_path(config, workdir, args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = initialize_database(db_path)
    register_builtins(connection)
    connection.close()

    if args.init_db_only:
        return 0

    web_enabled = _effective_web_enabled(config, args.web_enabled)
    if not web_enabled:
        return 0

    host = args.host or config.server_host()
    port = args.port or config.server_port()
    _run_server(db_path=db_path, host=host, port=port, config=config)
    return 0


def _effective_workdir(config: AppConfig, override: str | None) -> Path:
    if override is not None:
        return Path(override).expanduser().resolve()
    return config.runtime_workdir().expanduser().resolve()


def _effective_db_path(config: AppConfig, workdir: Path, override: str | None) -> Path:
    if override is not None:
        return Path(override).expanduser().resolve()

    configured_db = Path(config.runtime.get("db_path", "runtime/runtime.db"))
    if configured_db.is_absolute():
        return configured_db
    return (workdir / configured_db).resolve()


def _effective_web_enabled(config: AppConfig, override: bool | None) -> bool:
    if override is not None:
        return override
    return config.server_enabled()


def _run_server(db_path: Path, host: str, port: int, config: AppConfig) -> None:
    import logging

    from .app import create_app
    from .ingress import start_ingress_thread
    from .worker import start_worker_thread

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    llm_config = config.llm
    start_ingress_thread(str(db_path))
    start_worker_thread(str(db_path), llm_config)

    app = create_app(str(db_path))
    uvicorn = import_module("uvicorn")
    uvicorn.run(app, host=host, port=port)


def _write_default_config(path: Path) -> None:
    target_path = path.expanduser().resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    default_config = Path(__file__).resolve().parent / "defaults" / "app.yaml"
    target_path.write_text(default_config.read_text(encoding="utf-8"), encoding="utf-8")
