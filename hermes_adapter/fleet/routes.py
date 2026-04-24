"""HTTP handlers for /fleet/claim, /fleet/unclaim, /fleet/status.

Shared bearer auth: every endpoint requires ``Authorization: Bearer <key>``
matching ``$FLEET_CONTROL_KEY`` (or ``$BEARER_KEY`` as a fallback). If
neither is set, the endpoints reject every request — fail closed.

Mount into an aiohttp app via ``hermes_adapter.workspace.mount.mount_routes``
(or the dedicated ``mount_fleet_routes``) — they're registered alongside
the workspace routes on the same :8766 listener."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict

from aiohttp import web

from . import orchestrator as orc

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth — shared bearer, loaded from env at handler time (not import time) so
# a misconfigured env is visible at request time, not startup.
# ---------------------------------------------------------------------------


def _bearer() -> str:
    return (
        os.environ.get("FLEET_CONTROL_KEY")
        or os.environ.get("BEARER_KEY")
        or ""
    ).strip()


def _check_auth(request: web.Request) -> None:
    expected = _bearer()
    if not expected:
        raise web.HTTPInternalServerError(
            reason="fleet control bearer not configured on adapter"
        )
    got = (request.headers.get("Authorization") or "").strip()
    if not got.startswith("Bearer "):
        raise web.HTTPUnauthorized(reason="missing Bearer token")
    if got[len("Bearer ") :].strip() != expected:
        raise web.HTTPForbidden(reason="bad bearer")


# ---------------------------------------------------------------------------
# Error → HTTP mapping
# ---------------------------------------------------------------------------


def _error_response(exc: orc.FleetError) -> web.Response:
    logger.warning("[fleet] %s: %s", exc.reason, exc.message)
    return web.json_response(
        {"ok": False, "reason": exc.reason, "message": exc.message},
        status=exc.status,
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_claim(request: web.Request) -> web.Response:
    """POST /fleet/claim — body {agent, user}.

    Bind-mounts the user's workspace into the agent container and
    force-recreates it. Waits for the agent to pass /v1/health before
    returning."""
    _check_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    agent = (body.get("agent") or "").strip()
    user = (body.get("user") or "").strip()
    try:
        result = await orc.claim_agent(agent, user)
    except orc.FleetError as e:
        return _error_response(e)
    logger.info(
        "[fleet] claimed agent=%s user=%s container=%s",
        result.get("agent"),
        result.get("user"),
        result.get("container_id"),
    )
    return web.json_response(result)


async def handle_unclaim(request: web.Request) -> web.Response:
    """POST /fleet/unclaim — body {agent}.

    Swaps the agent back to the sentinel (empty) workspace mount so it
    no longer sees any user's files. Still running, still heartbeating —
    just unmounted."""
    _check_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    agent = (body.get("agent") or "").strip()
    try:
        result = await orc.unclaim_agent(agent)
    except orc.FleetError as e:
        return _error_response(e)
    logger.info("[fleet] unclaimed agent=%s", result.get("agent"))
    return web.json_response(result)


async def handle_status(request: web.Request) -> web.Response:
    """GET /fleet/status — returns every agent + its current_user
    (from the container label) + whether the container is running.

    Protected by bearer too: the current-user field is sensitive."""
    _check_auth(request)
    agent = request.query.get("agent")
    try:
        rows = await orc.get_status(agent)
    except orc.FleetError as e:
        return _error_response(e)
    return web.json_response({"ok": True, "agents": [asdict(r) for r in rows]})
