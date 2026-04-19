"""GET /ws — list every workspace directory under the configured root."""

from __future__ import annotations

import os

from aiohttp import web

from ..repo_finder import workspace_root


async def handle_list(request: web.Request) -> web.Response:
    """List workspace directories.

    Supports two layouts:
      - Flat:   ``{root}/{repo}``   (each direct child with ``.git`` is a project)
      - Nested: ``{root}/{user}/{repo}``  (GitHub-style ``user/repo`` hierarchy)
    """
    root = workspace_root()
    workspaces: list[dict] = []

    if os.path.isdir(root):
        try:
            for entry in sorted(os.scandir(root), key=lambda e: e.name.lower()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if os.path.isdir(os.path.join(entry.path, ".git")):
                    workspaces.append(
                        {"name": entry.name, "path": entry.name, "abs_path": entry.path}
                    )
                else:
                    try:
                        for child in sorted(os.scandir(entry.path), key=lambda e: e.name.lower()):
                            if not child.is_dir() or child.name.startswith("."):
                                continue
                            if os.path.isdir(os.path.join(child.path, ".git")):
                                workspaces.append(
                                    {
                                        "name": f"{entry.name}/{child.name}",
                                        "path": os.path.relpath(child.path, root),
                                        "abs_path": child.path,
                                    }
                                )
                    except PermissionError:
                        pass
        except PermissionError:
            pass

    return web.json_response({"status": "ok", "workspaces": workspaces, "root": root})
