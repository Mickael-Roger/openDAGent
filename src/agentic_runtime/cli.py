from __future__ import annotations

import argparse

import uvicorn

from .app import create_app
from .db import initialize_database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opendagent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db_parser = subparsers.add_parser("init-db", help="Initialize the runtime database")
    init_db_parser.add_argument("--db", default="runtime/runtime.db", help="SQLite database path")

    serve_parser = subparsers.add_parser("serve", help="Run the web interface")
    serve_parser.add_argument("--db", default="runtime/runtime.db", help="SQLite database path")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    serve_parser.add_argument("--port", default=8080, type=int, help="Bind port")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-db":
        connection = initialize_database(args.db)
        connection.close()
        return

    if args.command == "serve":
        app = create_app(args.db)
        uvicorn.run(app, host=args.host, port=args.port)
        return

    parser.error(f"Unsupported command: {args.command}")
