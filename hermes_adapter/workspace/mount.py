"""Attach workspace routes to an existing aiohttp ``web.Application``.

Use this from a host that already runs its own aiohttp app and wants the
workspace API mounted inline:

    from aiohttp import web
    from hermes_adapter.workspace.mount import mount_routes

    app = web.Application()
    mount_routes(app)
    web.run_app(app, port=8000)
"""

from __future__ import annotations

from aiohttp import web

from .routes import activate, file, git, init, listing, symbols, tree


def mount_routes(app: web.Application) -> None:
    """Register every ``/ws/*`` route onto *app*'s router."""
    r = app.router

    r.add_get("/ws", listing.handle_list)
    r.add_post("/ws/activate", activate.handle_activate)
    r.add_post("/ws/deactivate", activate.handle_deactivate)

    r.add_post("/ws/{repo}/init", init.handle_init)
    r.add_get("/ws/{repo}/tree", tree.handle_tree)

    r.add_get("/ws/{repo}/file", file.handle_get)
    r.add_post("/ws/{repo}/file", file.handle_post)
    r.add_delete("/ws/{repo}/file", file.handle_delete)

    r.add_get("/ws/{repo}/git/status", git.handle_status)
    r.add_post("/ws/{repo}/git/commit", git.handle_commit)
    r.add_post("/ws/{repo}/git/push", git.handle_push)
    r.add_post("/ws/{repo}/git/pull", git.handle_pull)
    r.add_post("/ws/{repo}/git/pr", git.handle_pr)
    r.add_get("/ws/{repo}/git/log", git.handle_log)
    r.add_get("/ws/{repo}/git/files", git.handle_files)

    r.add_get("/ws/{repo}/symbols", symbols.handle_get)
    r.add_post("/ws/{repo}/symbols/invalidate", symbols.handle_invalidate)
