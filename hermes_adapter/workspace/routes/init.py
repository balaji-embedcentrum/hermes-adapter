"""POST /ws/{repo}/init — clone an existing repo or create an empty one."""

from __future__ import annotations

import os

from aiohttp import web

from .. import proc
from ..repo_finder import find_repo, workspace_root


async def handle_init(request: web.Request) -> web.Response:
    repo = request.match_info["repo"]
    try:
        body = await request.json()
    except Exception:
        body = {}

    url = body.get("url", "")
    branch = body.get("branch", "main")

    existing = find_repo(repo)
    if existing:
        rc, out, err = await proc.run(["git", "pull", "--rebase", "origin", branch], existing)
        return web.json_response(
            {
                "status": "ok",
                "action": "pulled",
                "path": existing,
                "output": out.strip(),
                "error": err.strip() if rc else None,
            }
        )

    is_empty = bool(body.get("empty", False))
    if not url and not is_empty:
        return web.json_response(
            {"status": "error", "message": "Repo not found and no clone URL provided"},
            status=404,
        )

    if is_empty:
        root = workspace_root()
        user_dir: str | None = None
        if os.path.isdir(root):
            for entry in sorted(os.scandir(root), key=lambda e: e.name):
                if entry.is_dir() and entry.name not in (".git", "local"):
                    user_dir = entry.path
                    break
        if not user_dir:
            user_dir = f"{root}/user"

        dest = f"{user_dir}/{repo}"
        os.makedirs(dest, exist_ok=True)
        await proc.run(["git", "init"], dest)
        gitkeep = os.path.join(dest, ".gitkeep")
        if not os.path.exists(gitkeep):
            with open(gitkeep, "w") as f:
                f.write("")
        return web.json_response({"status": "ok", "action": "created", "path": dest})

    # Extract username from GitHub URL: https://github.com/{owner}/{repo}.git
    owner = "user"
    if "github.com/" in url:
        parts = url.split("github.com/")[-1].split("/")
        if parts:
            owner = parts[0]

    root = workspace_root()
    dest_dir = f"{root}/{owner}"
    os.makedirs(dest_dir, exist_ok=True)
    dest = f"{dest_dir}/{repo}"

    rc, out, err = await proc.run(["git", "clone", "--branch", branch, url, dest], dest_dir)
    if rc == 0:
        return web.json_response({"status": "ok", "action": "cloned", "path": dest})
    return web.json_response({"status": "error", "message": err.strip()}, status=500)
