from __future__ import annotations

import argparse
import shutil
from importlib import import_module
from pathlib import Path

import yaml

from .capabilities import load_and_register
from .config import AppConfig, load_app_config
from .db import initialize_database
from .tracing import init_trace_db


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
    parser.add_argument(
        "--add-provider",
        action="store_true",
        help="Interactively add an LLM provider to the config file and exit",
    )
    parser.add_argument(
        "--chatgpt-login",
        action="store_true",
        help="Authenticate with your ChatGPT subscription via device flow and exit",
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

    if args.chatgpt_login:
        from .chatgpt_auth import login_device_flow
        login_device_flow()
        _ensure_chatgpt_provider(config_path)
        return 0

    if args.add_provider:
        return _add_provider_wizard(config_path)

    config = load_app_config(config_path)

    workdir = _effective_workdir(config, args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    db_path = _effective_db_path(config, workdir, args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Initialise trace database (separate from runtime DB)
    trace_db = db_path.parent / "traces.db"
    init_trace_db(trace_db)

    user_caps_dir = workdir / "config" / "capabilities"
    connection = initialize_database(db_path)
    load_and_register(
        connection,
        [user_caps_dir] if user_caps_dir.exists() else None,
        llm_config=config.llm,
        mcp_config=config.mcp,
    )
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
    import atexit
    import logging

    from .app import create_app
    from .ingress import start_ingress_thread
    from .worker import start_worker_thread

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log = logging.getLogger(__name__)

    app_config = {"llm": config.llm, "mcp": config.mcp}
    user_caps_dir = config.runtime_workdir() / "config" / "capabilities"
    extra_dirs = [str(user_caps_dir)] if user_caps_dir.exists() else None

    # ── Email tool initialisation ────────────────────────────────────────────
    email_cfg = config.email
    if email_cfg.get("enabled", False):
        import os
        from .tools import email as _email_tools
        os.environ["OPENDAGENT_EMAIL_ENABLED"] = "1"
        _email_tools.configure(email_cfg)
        log.info("Email tools enabled (IMAP: %s).", email_cfg.get("imap", {}).get("host", "?"))

    # ── opencode availability check ──────────────────────────────────────────
    opencode_cfg = config.opencode
    opencode_enabled = opencode_cfg.get("enabled", True)
    opencode_available = shutil.which("opencode") is not None

    if not opencode_available:
        log.warning(
            "opencode binary not found in PATH — coding capabilities (code, test_code, "
            "code_review, debug_code) will be hidden. Install opencode to enable them: "
            "https://opencode.ai/docs"
        )
    elif opencode_enabled:
        from .opencode.server import init_server, shutdown_server
        oc_port = int(opencode_cfg.get("port", 9180))
        oc_model_hint = opencode_cfg.get("model_hint") or None
        init_server(config.llm, port=oc_port, model_hint=oc_model_hint)
        atexit.register(shutdown_server)
    else:
        log.info("opencode is installed but disabled in config (opencode.enabled: false).")

    # ── Start background threads ─────────────────────────────────────────────
    start_ingress_thread(str(db_path))
    start_worker_thread(str(db_path), app_config)

    app = create_app(
        str(db_path),
        extra_capability_dirs=extra_dirs,
        user_caps_dir=str(user_caps_dir),
        mcp_config=config.mcp,
        llm_config=config.llm,
    )
    uvicorn = import_module("uvicorn")
    uvicorn.run(app, host=host, port=port)


def _write_default_config(path: Path) -> None:
    target_path = path.expanduser().resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    default_config = Path(__file__).resolve().parent / "defaults" / "app.yaml"
    target_path.write_text(default_config.read_text(encoding="utf-8"), encoding="utf-8")


# ── --add-provider interactive wizard ────────────────────────────────────────

_PROVIDER_PRESETS: dict[str, tuple[str, str, str]] = {
    # key: (wire_type, display_label, default_endpoint)
    "1":  ("openai",    "OpenAI",                      "https://api.openai.com/v1"),
    "2":  ("anthropic", "Anthropic",                   "https://api.anthropic.com"),
    "3":  ("openai",    "Google Gemini (AI Studio)",   "https://generativelanguage.googleapis.com/v1beta/openai"),
    "4":  ("openai",    "Mistral",                     "https://api.mistral.ai/v1"),
    "5":  ("openai",    "Azure OpenAI",                ""),
    "6":  ("openai",    "MiniMax",                     "https://api.minimaxi.com/v1"),
    "7":  ("openai",    "Zhipu AI / GLM (Z.AI)",       "https://open.bigmodel.cn/api/paas/v4"),
    "8":  ("openai",    "Local / Ollama / vLLM",       "http://localhost:11434/v1"),
    "9":  ("openai",    "Other OpenAI-compatible",     ""),
}

_DEFAULT_IDS: dict[str, str] = {
    "1": "openai", "2": "anthropic", "3": "google", "4": "mistral",
    "5": "azure",  "6": "minimax",   "7": "zhipu",  "8": "local", "9": "custom",
}

_DEFAULT_ENV_VARS: dict[str, str] = {
    "1": "OPENAI_API_KEY",   "2": "ANTHROPIC_API_KEY", "3": "GOOGLE_API_KEY",
    "4": "MISTRAL_API_KEY",  "5": "AZURE_OPENAI_API_KEY", "6": "MINIMAX_API_KEY",
    "7": "ZHIPU_API_KEY",    "8": "",  "9": "API_KEY",
}

_LLM_FEATURES = [
    "vision", "reasoning", "json_mode", "long_context",
    "code", "image_generation", "native_web_search",
]

_ROLES = ["strong_reasoning", "balanced", "cheap_fast", "image_generation"]


def _prompt(label: str, default: str = "") -> str:
    display = f" [{default}]" if default else ""
    value = input(f"{label}{display}: ").strip()
    return value or default


class _IndentedDumper(yaml.Dumper):
    """PyYAML dumper that indents list items under their parent key."""
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:  # type: ignore[override]
        return super().increase_indent(flow=flow, indentless=False)


def _yaml_dump(data: Any) -> str:
    return yaml.dump(
        data,
        Dumper=_IndentedDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


def _add_provider_wizard(config_path: Path) -> int:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print("ERROR: Could not parse config file.")
        return 1

    data.setdefault("llm", {})
    data["llm"].setdefault("providers", [])

    print("\n╔══════════════════════════════╗")
    print("║   openDAGent — Add Provider  ║")
    print("╚══════════════════════════════╝\n")

    # ── Choose provider type ──────────────────────────────────────────────────
    print("Provider type:")
    for k, (_, label, _) in _PROVIDER_PRESETS.items():
        print(f"  {k}) {label}")
    choice = input("\nChoice [1-9]: ").strip()
    if choice not in _PROVIDER_PRESETS:
        print("Invalid choice. Aborted.")
        return 1

    ptype, _label, default_endpoint = _PROVIDER_PRESETS[choice]

    # ── Basic info ────────────────────────────────────────────────────────────
    pid      = _prompt("Provider ID", _DEFAULT_IDS[choice])
    endpoint = _prompt("Endpoint URL", default_endpoint)

    # ── Auth ──────────────────────────────────────────────────────────────────
    if choice == "8":   # local — no auth
        auth: dict = {"type": "none"}
    else:
        env_var = _prompt("API key environment variable", _DEFAULT_ENV_VARS.get(choice, "API_KEY"))
        auth = {"type": "api_key", "env_var": env_var}

    # ── Models ────────────────────────────────────────────────────────────────
    models: list[dict] = []
    feat_list = "  ".join(f"{i+1}={f}" for i, f in enumerate(_LLM_FEATURES))
    role_list = "  ".join(f"{i+1}={r}" for i, r in enumerate(_ROLES))

    print("\nAdd models (press Enter with empty ID when done):")
    while True:
        model_id = input("  Model ID: ").strip()
        if not model_id:
            if not models:
                print("  (at least one model is required)")
                continue
            break

        print(f"  Roles:    {role_list}")
        role_input = input("  Role [1]: ").strip()
        try:
            role = _ROLES[int(role_input) - 1] if role_input else _ROLES[0]
        except (ValueError, IndexError):
            role = "balanced"

        print(f"  Features: {feat_list}")
        feat_input = input("  Features (e.g. 1,3,5 — or Enter for none): ").strip()
        features: list[str] = []
        for part in feat_input.split(","):
            try:
                idx = int(part.strip()) - 1
                if 0 <= idx < len(_LLM_FEATURES):
                    features.append(_LLM_FEATURES[idx])
            except ValueError:
                pass

        models.append({"id": model_id, "role": role, "features": features})
        print(f"  + {model_id}  role={role}  features={features or []}")

    # ── Summary + confirm ─────────────────────────────────────────────────────
    provider: dict = {
        "id": pid,
        "type": ptype,
        "endpoint": endpoint,
        "auth": auth,
        "models": models,
    }

    print("\n--- Provider to add ---")
    print(_yaml_dump(provider).rstrip())
    print("-----------------------")

    confirm = input("\nAdd to config? [Y/n]: ").strip().lower()
    if confirm == "n":
        print("Aborted.")
        return 0

    # ── Write back ────────────────────────────────────────────────────────────
    data["llm"]["providers"].append(provider)

    # Set defaults if none exist yet
    if not data["llm"].get("default_provider"):
        data["llm"]["default_provider"] = pid
        data["llm"]["default_model"] = models[0]["id"]
        print(f"  default_provider → {pid},  default_model → {models[0]['id']}")

    config_path.write_text(_yaml_dump(data), encoding="utf-8")
    print(f"\nProvider '{pid}' added to {config_path}")
    return 0


# ── ChatGPT provider auto-registration ─────────────────────────────────────

_CHATGPT_PROVIDER: dict = {
    "id": "chatgpt",
    "type": "chatgpt",
    "models": [
        {
            "id": "gpt-4o",
            "role": "strong_reasoning",
            "features": ["vision", "json_mode", "long_context", "code"],
            "cost": 0,
            "speed": 8,
            "scores": {"reasoning": 9, "coding": 9, "writing": 8},
        },
        {
            "id": "o3",
            "role": "strong_reasoning",
            "features": ["reasoning", "json_mode", "long_context", "code"],
            "cost": 0,
            "speed": 4,
            "scores": {"reasoning": 10, "coding": 10},
        },
    ],
}


def _ensure_chatgpt_provider(config_path: Path) -> None:
    """Add the chatgpt provider to config.yaml if not already present."""
    if not config_path.exists():
        return

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return

    data.setdefault("llm", {})
    providers: list = data["llm"].setdefault("providers", [])

    # Skip if a chatgpt-type provider already exists
    for p in providers:
        if isinstance(p, dict) and p.get("type") == "chatgpt":
            print(f"ChatGPT provider already configured in {config_path}")
            return

    providers.append(_CHATGPT_PROVIDER)

    # Set as default if no default exists yet
    if not data["llm"].get("default_provider"):
        data["llm"]["default_provider"] = "chatgpt"
        data["llm"]["default_model"] = "gpt-4o"
        print(f"  default_provider → chatgpt,  default_model → gpt-4o")

    config_path.write_text(_yaml_dump(data), encoding="utf-8")
    print(f"ChatGPT provider added to {config_path}")
