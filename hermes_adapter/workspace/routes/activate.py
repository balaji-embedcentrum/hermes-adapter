"""POST /ws/activate, POST /ws/deactivate — switch the active-user symlink.

When deployed with a root like ``/workspaces/active`` that is a symlink to
``/workspaces/{user}``, an authenticating proxy can rebind which user is
currently active without bouncing the adapter. This is how Hermes Studio
keeps per-user isolation cheap.
"""

from __future__ import annotations

import os
import shutil

from aiohttp import web

from ..repo_finder import workspace_root


def _active_paths() -> tuple[str, str]:
    """Return (mount_root, active_link) for the current config.

    ``mount_root`` is the parent of ``/active`` when the workspace root is
    ``.../active``; otherwise it is the workspace root itself.
    """
    root = workspace_root()
    mount_root = os.path.dirname(root) if root.rstrip("/").endswith("/active") else root
    active_link = os.path.join(mount_root, "active")
    return mount_root, active_link


async def handle_activate(request: web.Request) -> web.Response:
    """Symlink ``{mount}/active`` → ``{mount}/{user}``. Body: ``{user: str}``."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    user = (body.get("user") or "").strip()
    if not user or "/" in user or ".." in user:
        return web.json_response({"status": "error", "message": "Invalid user"}, status=400)

    mount_root, active_link = _active_paths()
    user_dir = os.path.join(mount_root, user)
    os.makedirs(user_dir, exist_ok=True)

    try:
        if os.path.islink(active_link):
            os.unlink(active_link)
        elif os.path.isdir(active_link):
            shutil.rmtree(active_link, ignore_errors=True)
        elif os.path.exists(active_link):
            os.unlink(active_link)
    except OSError:
        pass

    os.symlink(user_dir, active_link)
    return web.json_response(
        {"status": "ok", "user": user, "active": active_link, "target": user_dir}
    )


async def handle_deactivate(request: web.Request) -> web.Response:
    """Remove the ``active`` symlink — after this the agent sees no user files."""
    _, active_link = _active_paths()
    try:
        if os.path.islink(active_link):
            os.unlink(active_link)
    except OSError:
        pass
    return web.json_response({"status": "ok"})
