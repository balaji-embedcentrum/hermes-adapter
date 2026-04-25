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

import os

from aiohttp import web

from ..fleet import routes as fleet_routes
from ..proxy import routes as proxy_routes
from .routes import activate, file, git, init, listing, symbols, tree


def mount_routes(app: web.Application) -> None:
    """Register every ``/ws/*`` route onto *app*'s router.

    When the adapter is running in multi-tenant fleet mode
    (``HERMES_FLEET_MODE=1`` or ``FLEET_ROOT`` set), also register the
    ``/fleet/*`` control plane that orchestrates per-session bind mounts
    via ``docker compose``. Single-user installs leave those env vars
    unset and only get the workspace API.
    """
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
    r.add_get("/ws/{repo}/git/diff", git.handle_diff)
    r.add_get("/ws/{repo}/git/branches", git.handle_branches)
    r.add_get("/ws/{repo}/git/show/{sha}", git.handle_show)

    r.add_get("/ws/{repo}/symbols", symbols.handle_get)
    r.add_post("/ws/{repo}/symbols/invalidate", symbols.handle_invalidate)

    # Fleet control plane — opt-in via env.
    if os.environ.get("HERMES_FLEET_MODE") or os.environ.get("FLEET_ROOT"):
        r.add_post("/fleet/claim", fleet_routes.handle_claim)
        r.add_post("/fleet/unclaim", fleet_routes.handle_unclaim)
        r.add_get("/fleet/status", fleet_routes.handle_status)

        # LLM provider proxy — keeps provider keys out of agent containers.
        # Same fleet-mode gate: this only makes sense when the adapter has
        # the on-host ``agents/<name>/.env`` files mounted. ``route("*")``
        # covers POST (chat) + GET (models). The path tail is captured
        # verbatim and appended to the provider's upstream base URL.
        r.add_route("*", "/proxy/{agent}/{provider}/{path:.*}", proxy_routes.handle_proxy)
