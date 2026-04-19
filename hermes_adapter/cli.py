"""CLI entry point: ``hermes-adapter serve``.

Subcommands:
  serve        Run workspace API and (optionally) A2A server concurrently
  workspace    Run only the workspace API
  a2a          Run only the A2A server
  version      Print version
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import load as load_config


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
    """Run workspace + A2A in the same process (two asyncio servers)."""
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
                uv_cfg = uvicorn.Config(
                    a2a_app, host=a2a_host, port=a2a_port, log_level="warning"
                )
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


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"hermes-adapter {__version__}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-adapter",
        description="Sidecar adapter for hermes-agent (A2A + workspace API).",
    )
    parser.add_argument("--log-level", default="INFO", help="Log level (default: INFO)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="Run workspace + A2A together")
    p_serve.add_argument("--workspace-host")
    p_serve.add_argument("--workspace-port", type=int)
    p_serve.add_argument("--a2a-host")
    p_serve.add_argument("--a2a-port", type=int)
    p_serve.add_argument("--no-a2a", action="store_true", help="Disable A2A server")
    p_serve.set_defaults(func=cmd_serve)

    p_ws = sub.add_parser("workspace", help="Run only the workspace API")
    p_ws.add_argument("--host")
    p_ws.add_argument("--port", type=int)
    p_ws.set_defaults(func=cmd_workspace)

    p_a2a = sub.add_parser("a2a", help="Run only the A2A server")
    p_a2a.add_argument("--host")
    p_a2a.add_argument("--port", type=int)
    p_a2a.set_defaults(func=cmd_a2a)

    p_ver = sub.add_parser("version", help="Print version")
    p_ver.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.log_level)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
