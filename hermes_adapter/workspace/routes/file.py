"""File CRUD routes.

    GET    /ws/{repo}/file?path=
    POST   /ws/{repo}/file       body: {path, content}
    DELETE /ws/{repo}/file?path=
"""

from __future__ import annotations

import os
import shutil

from aiohttp import web

from .. import symbols_cache
from ..repo_finder import find_repo, resolve_safe_path


async def handle_get(request: web.Request) -> web.Response:
    repo = request.match_info["repo"]
    rel = request.rel_url.query.get("path", "")
    if not rel:
        return web.json_response(
            {"status": "error", "message": "path query param required"}, status=400
        )

    workspace = find_repo(repo)
    if not workspace:
        return web.json_response(
            {"status": "error", "message": f"Workspace for '{repo}' not found"}, status=404
        )

    abs_path = resolve_safe_path(workspace, rel)
    if abs_path is None:
        return web.json_response(
            {"status": "error", "message": "Path traversal not allowed"}, status=403
        )
    if not os.path.isfile(abs_path):
        return web.json_response({"status": "error", "message": "File not found"}, status=404)

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return web.json_response({"status": "ok", "path": rel, "content": content})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_post(request: web.Request) -> web.Response:
    repo = request.match_info["repo"]
    workspace = find_repo(repo)
    if not workspace:
        return web.json_response(
            {"status": "error", "message": f"Workspace for '{repo}' not found"}, status=404
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON body"}, status=400)

    rel = body.get("path", "")
    content = body.get("content", "")
    if not rel:
        return web.json_response({"status": "error", "message": "path required"}, status=400)

    abs_path = resolve_safe_path(workspace, rel)
    if abs_path is None:
        return web.json_response(
            {"status": "error", "message": "Path traversal not allowed"}, status=403
        )

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        symbols_cache.invalidate(repo)
        return web.json_response({"status": "ok", "path": rel, "written": True})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_delete(request: web.Request) -> web.Response:
    repo = request.match_info["repo"]
    workspace = find_repo(repo)
    if not workspace:
        return web.json_response(
            {"status": "error", "message": f"Workspace for '{repo}' not found"}, status=404
        )

    rel = request.rel_url.query.get("path", "")
    if not rel:
        return web.json_response({"status": "error", "message": "path required"}, status=400)

    abs_path = resolve_safe_path(workspace, rel)
    if abs_path is None:
        return web.json_response(
            {"status": "error", "message": "Path traversal not allowed"}, status=403
        )

    try:
        if os.path.isfile(abs_path):
            os.remove(abs_path)
        elif os.path.isdir(abs_path):
            shutil.rmtree(abs_path)
        else:
            return web.json_response({"status": "error", "message": "File not found"}, status=404)
        symbols_cache.invalidate(repo)
        return web.json_response({"status": "ok", "path": rel, "deleted": True})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)
