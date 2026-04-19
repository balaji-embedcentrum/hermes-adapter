"""GET /ws/{repo}/tree?path= — one-level directory listing."""

from __future__ import annotations

import os

from aiohttp import web

from ..repo_finder import find_repo


async def handle_tree(request: web.Request) -> web.Response:
    repo = request.match_info["repo"]
    rel = request.rel_url.query.get("path", "")

    workspace = find_repo(repo)
    if not workspace:
        return web.json_response(
            {"status": "error", "message": f"Workspace for '{repo}' not found"}, status=404
        )

    target = os.path.join(workspace, rel) if rel else workspace
    if not os.path.isdir(target):
        return web.json_response(
            {"status": "error", "message": "Path is not a directory"}, status=400
        )

    entries: list[dict] = []
    try:
        for entry in sorted(
            os.scandir(target), key=lambda e: (not e.is_dir(), e.name.lower())
        ):
            if entry.name.startswith(".git") and entry.name != ".gitignore":
                continue
            rel_path = os.path.relpath(entry.path, workspace)
            entries.append(
                {
                    "name": entry.name,
                    "path": rel_path,
                    "type": "dir" if entry.is_dir() else "file",
                }
            )
    except PermissionError as e:
        return web.json_response({"status": "error", "message": str(e)}, status=403)

    return web.json_response({"status": "ok", "path": rel, "entries": entries})
