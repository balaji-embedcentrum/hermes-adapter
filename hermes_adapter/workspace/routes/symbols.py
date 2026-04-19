"""Sylang symbols routes — batch file delivery with TTL cache.

    GET  /ws/{repo}/symbols              returns all Sylang files + content
    POST /ws/{repo}/symbols/invalidate   busts the cache for a repo
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from .. import symbols_cache
from ..repo_finder import find_repo

logger = logging.getLogger(__name__)


async def handle_get(request: web.Request) -> web.Response:
    repo = request.match_info["repo"]
    workspace = find_repo(repo)
    if not workspace:
        return web.json_response(
            {"status": "error", "message": f"Workspace for '{repo}' not found"}, status=404
        )

    # The walk is blocking I/O — run it in the default executor
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, symbols_cache.get_or_build, repo, workspace)
    logger.info(
        "[ws/symbols] %s: delivered %d Sylang files", repo, result.get("fileCount", 0)
    )
    return web.json_response(result)


async def handle_invalidate(request: web.Request) -> web.Response:
    repo = request.match_info["repo"]
    symbols_cache.invalidate(repo)
    return web.json_response({"status": "ok", "repo": repo})
