"""Standalone aiohttp app for the workspace API.

Entry points:
    run(host, port)           start a blocking server
    build_app()               build an aiohttp.web.Application (for composition)
    main()                    console script: ``hermes-adapter-workspace``
"""

from __future__ import annotations

import logging
import os
import sys

from aiohttp import web

from ..config import load as load_config
from .mount import mount_routes

logger = logging.getLogger(__name__)


async def _handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "hermes-adapter-workspace"})


def build_app() -> web.Application:
    """Build the aiohttp application with workspace routes + /health."""
    app = web.Application()
    app.router.add_get("/health", _handle_health)
    mount_routes(app)
    return app


def run(host: str | None = None, port: int | None = None) -> None:
    """Start the workspace server (blocking)."""
    cfg = load_config()
    host = host or cfg.workspace_host
    port = port or cfg.workspace_port

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            stream=sys.stderr,
        )

    logger.info("workspace API listening on http://%s:%d", host, port)
    logger.info("workspace root: %s", os.environ.get("HERMES_WORKSPACE_DIR", "/workspaces"))

    app = build_app()
    web.run_app(app, host=host, port=port, print=lambda *_: None)


def main() -> None:
    """Console script entry: ``hermes-adapter-workspace``."""
    run()


if __name__ == "__main__":
    main()
