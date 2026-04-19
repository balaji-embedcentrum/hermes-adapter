"""Workspace HTTP API — filesystem + git + Sylang symbols, no LLM.

Public API:
    build_app()       build an aiohttp.web.Application with all /ws/* routes + /health
    mount_routes(app) attach /ws/* routes onto an existing aiohttp app
    run(host, port)   start a blocking server on host:port
"""

from .app import build_app, run
from .mount import mount_routes

__all__ = ["build_app", "mount_routes", "run"]
