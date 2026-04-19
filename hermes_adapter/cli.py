"""CLI entry point: ``hermes-adapter``.

Subcommands:
  init              Scaffold ~/.hermes-adapter with a fresh agents.yaml
  agent add         Add an agent entry + create its HERMES_HOME folder
  agent remove      Delete an agent entry (and optionally its folder)
  agent list        Print every configured agent
  up                Start the workspace API + every configured agent
  down              Stop everything started by `up`
  status            Show what's running
  serve             (power user) Run workspace + optional A2A in-process
  workspace         (power user) Run only the workspace API
  a2a               (power user) Run only the A2A server
  version           Print version
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import load as load_config
from .manifest import (
    DEFAULT_AGENTS_DIR,
    DEFAULT_HOME,
    DEFAULT_LOG_DIR,
    DEFAULT_MANIFEST,
    DEFAULT_RUN_DIR,
    AgentSpec,
    Manifest,
    default_manifest,
    provider_env_var,
    write_agent_home,
)


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    home = Path(os.path.expanduser(args.dir)) if args.dir else DEFAULT_HOME
    manifest_path = home / "agents.yaml"

    if manifest_path.exists() and not args.force:
        print(f"{manifest_path} already exists. Pass --force to overwrite.", file=sys.stderr)
        return 1

    home.mkdir(parents=True, exist_ok=True)
    DEFAULT_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_RUN_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)

    manifest = default_manifest()
    if args.workspace_dir:
        manifest.adapter.workspace_dir = str(Path(os.path.expanduser(args.workspace_dir)))
    if args.cors_origins:
        manifest.adapter.cors_origins = [o.strip() for o in args.cors_origins.split(",") if o.strip()]
    if args.adapter_port:
        manifest.adapter.port = args.adapter_port

    manifest.save(manifest_path)
    Path(manifest.adapter.workspace_dir).mkdir(parents=True, exist_ok=True)

    print(f"✓ wrote {manifest_path}")
    print(f"  adapter:        http://{manifest.adapter.host}:{manifest.adapter.port}")
    print(f"  workspace dir:  {manifest.adapter.workspace_dir}")
    print(f"  cors origins:   {', '.join(manifest.adapter.cors_origins)}")
    print(f"  a2a bearer:     {manifest.a2a_key}")
    print()
    print("next:")
    print("  hermes-adapter agent add alpha --model anthropic/claude-sonnet-4.6")
    print("  hermes-adapter up")
    return 0


# ---------------------------------------------------------------------------
# agent add / remove / list
# ---------------------------------------------------------------------------

def cmd_agent_add(args: argparse.Namespace) -> int:
    manifest = Manifest.load(args.manifest)
    port = args.port or manifest.next_free_port()

    spec = AgentSpec(
        name=args.name,
        port=port,
        model=args.model,
        description=args.description or "",
        hermes_home=str(DEFAULT_AGENTS_DIR / args.name),
    )
    try:
        manifest.add(spec)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    provider_key = args.key
    if not provider_key and args.prompt_key:
        var = provider_env_var(args.model)
        provider_key = getpass.getpass(f"Paste {var} (hidden): ").strip()

    write_agent_home(spec, provider_key=provider_key or None, base_url=args.base_url or None)
    manifest.save(args.manifest)

    var = provider_env_var(args.model)
    print(f"✓ added agent '{args.name}' on port {port}")
    print(f"  HERMES_HOME:   {spec.hermes_home}")
    print(f"  model:         {args.model}")
    if not provider_key:
        print(f"  ⚠  no API key set — edit {spec.hermes_home}/.env and set {var}")
    return 0


def cmd_agent_remove(args: argparse.Namespace) -> int:
    manifest = Manifest.load(args.manifest)
    try:
        spec = manifest.remove(args.name)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    manifest.save(args.manifest)
    print(f"✓ removed '{args.name}' from manifest")
    if args.purge:
        import shutil
        home = spec.resolved_home()
        shutil.rmtree(home, ignore_errors=True)
        print(f"  purged {home}")
    else:
        print(f"  (HERMES_HOME folder at {spec.resolved_home()} kept — pass --purge to delete)")
    return 0


def cmd_agent_list(args: argparse.Namespace) -> int:
    try:
        manifest = Manifest.load(args.manifest)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not manifest.agents:
        print("(no agents configured yet — add one with `hermes-adapter agent add <name> --model <model>`)")
        return 0

    name_w = max(len(a.name) for a in manifest.agents) + 2
    model_w = max(len(a.model) for a in manifest.agents) + 2
    print(f"{'NAME':<{name_w}}{'PORT':<8}{'MODEL':<{model_w}}DESCRIPTION")
    for a in manifest.agents:
        print(f"{a.name:<{name_w}}{a.port:<8}{a.model:<{model_w}}{a.description}")
    return 0


# ---------------------------------------------------------------------------
# up / down / status (delegate to supervisor)
# ---------------------------------------------------------------------------

def cmd_up(args: argparse.Namespace) -> int:
    from .supervisor import Supervisor

    manifest = Manifest.load(args.manifest)
    Supervisor(manifest).run(detach=args.detach)
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    from .supervisor import Supervisor

    return 0 if Supervisor.stop_running() else 1


def cmd_status(args: argparse.Namespace) -> int:
    from .supervisor import Supervisor

    Supervisor.print_status()
    return 0


# ---------------------------------------------------------------------------
# power-user subcommands (kept from original CLI)
# ---------------------------------------------------------------------------

def cmd_workspace(args: argparse.Namespace) -> int:
    from .workspace.app import run as run_workspace

    cfg = load_config()
    run_workspace(host=args.host or cfg.workspace_host, port=args.port or cfg.workspace_port)
    return 0


def cmd_a2a(args: argparse.Namespace) -> int:
    try:
        from .a2a.entry import run as run_a2a
    except ImportError as e:
        print(
            f"error: A2A extras are not installed ({e}).\n"
            "       Install with: pip install 'hermes-adapter[a2a]'",
            file=sys.stderr,
        )
        return 1
    cfg = load_config()
    run_a2a(host=args.host or cfg.a2a_host, port=args.port or cfg.a2a_port)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Run workspace + A2A from env vars (no manifest needed)."""
    import asyncio

    from .workspace.app import build_app as build_ws_app

    cfg = load_config()
    ws_host = args.workspace_host or cfg.workspace_host
    ws_port = args.workspace_port or cfg.workspace_port

    async def _serve() -> None:
        from aiohttp import web

        ws_app = build_ws_app()
        ws_runner = web.AppRunner(ws_app)
        await ws_runner.setup()
        ws_site = web.TCPSite(ws_runner, host=ws_host, port=ws_port)
        await ws_site.start()
        logging.getLogger(__name__).info(
            "workspace API listening on http://%s:%d", ws_host, ws_port
        )

        a2a_task = None
        if not args.no_a2a:
            try:
                from .a2a.server import build_app as build_a2a_app
                import uvicorn

                a2a_host = args.a2a_host or cfg.a2a_host
                a2a_port = args.a2a_port or cfg.a2a_port
                a2a_app = build_a2a_app(port=a2a_port)
                uv_cfg = uvicorn.Config(a2a_app, host=a2a_host, port=a2a_port, log_level="warning")
                server = uvicorn.Server(uv_cfg)
                a2a_task = asyncio.create_task(server.serve())
                logging.getLogger(__name__).info(
                    "A2A server listening on http://%s:%d", a2a_host, a2a_port
                )
            except ImportError:
                logging.getLogger(__name__).warning(
                    "A2A extras not installed — skipping A2A server. "
                    "Install with: pip install 'hermes-adapter[a2a]'"
                )
            except RuntimeError as e:
                logging.getLogger(__name__).warning("A2A startup failed: %s", e)

        try:
            if a2a_task is not None:
                await a2a_task
            else:
                while True:
                    await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await ws_runner.cleanup()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass
    return 0


def cmd_compose_generate(args: argparse.Namespace) -> int:
    from .compose import dump_compose

    manifest = Manifest.load(args.manifest)
    text = dump_compose(
        manifest,
        image=args.image,
        hermes_agent_image=args.hermes_agent_image,
        bind_address=args.bind,
    )
    if args.output and args.output != "-":
        Path(args.output).write_text(text)
        print(f"✓ wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"hermes-adapter {__version__}")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-adapter",
        description="Sidecar adapter for hermes-agent (workspace API + A2A + multi-agent supervisor).",
    )
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    # --- init ---
    p = sub.add_parser("init", help="Scaffold ~/.hermes-adapter with a fresh agents.yaml")
    p.add_argument("--dir", help="Override HERMES_ADAPTER_HOME (default: ~/.hermes-adapter)")
    p.add_argument("--workspace-dir", help="Workspace root (default: ~/hermes-workspaces)")
    p.add_argument("--cors-origins", help="Comma-separated CORS origins")
    p.add_argument("--adapter-port", type=int, help="Workspace API port (default: 8766)")
    p.add_argument("--force", action="store_true", help="Overwrite existing manifest")
    p.set_defaults(func=cmd_init)

    # --- agent {add, remove, list} ---
    p_agent = sub.add_parser("agent", help="Manage configured agents")
    p_agent_sub = p_agent.add_subparsers(dest="agent_command", required=True)

    p = p_agent_sub.add_parser("add", help="Add an agent + create its HERMES_HOME")
    p.add_argument("name")
    p.add_argument("--model", required=True, help="e.g. anthropic/claude-sonnet-4.6")
    p.add_argument("--port", type=int, help="A2A port (default: auto-picks next free from 9001)")
    p.add_argument("--description", help="Short description shown in the Agent Card")
    p.add_argument("--key", help="Provider API key (otherwise edit .env later)")
    p.add_argument("--prompt-key", action="store_true", help="Prompt for the provider key interactively")
    p.add_argument("--base-url", help="OPENAI_BASE_URL for local / self-hosted OpenAI-compatible endpoints")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.set_defaults(func=cmd_agent_add)

    p = p_agent_sub.add_parser("remove", help="Delete an agent from the manifest")
    p.add_argument("name")
    p.add_argument("--purge", action="store_true", help="Also delete its HERMES_HOME folder")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.set_defaults(func=cmd_agent_remove)

    p = p_agent_sub.add_parser("list", help="Print every configured agent")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.set_defaults(func=cmd_agent_list)

    # --- up / down / status ---
    p = sub.add_parser("up", help="Start workspace API + every configured agent")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--detach", action="store_true", help="Run in the background")
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("down", help="Stop everything started by `up`")
    p.set_defaults(func=cmd_down)

    p = sub.add_parser("status", help="Show what's running")
    p.set_defaults(func=cmd_status)

    # --- power-user subcommands (unchanged) ---
    p = sub.add_parser("serve", help="Run workspace + A2A from env vars (no manifest)")
    p.add_argument("--workspace-host")
    p.add_argument("--workspace-port", type=int)
    p.add_argument("--a2a-host")
    p.add_argument("--a2a-port", type=int)
    p.add_argument("--no-a2a", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("workspace", help="Run only the workspace API")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_workspace)

    p = sub.add_parser("a2a", help="Run only the A2A server")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_a2a)

    # --- compose generate ---
    p_compose = sub.add_parser("compose", help="Docker-compose helpers")
    p_compose_sub = p_compose.add_subparsers(dest="compose_command", required=True)
    p = p_compose_sub.add_parser("generate", help="Emit docker-compose.yml from agents.yaml")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("-o", "--output", default="-", help="Destination file (default: stdout)")
    p.add_argument("--image", default="ghcr.io/balaji-embedcentrum/hermes-adapter:latest",
                   help="Adapter image tag")
    p.add_argument("--hermes-agent-image", default="noushermes/hermes-agent:latest",
                   help="hermes-agent image tag")
    p.add_argument("--bind", default="127.0.0.1",
                   help="Host bind address (use 0.0.0.0 to expose publicly; default: 127.0.0.1)")
    p.set_defaults(func=cmd_compose_generate)

    p = sub.add_parser("version", help="Print version")
    p.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.log_level)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
