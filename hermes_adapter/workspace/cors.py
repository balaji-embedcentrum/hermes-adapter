"""CORS middleware for the workspace aiohttp app.

Exists so that hosted browsers (e.g. Hermes Studio at
``https://studio.example.com``) can call a workspace API running on the
user's own laptop (``http://127.0.0.1:8766``) without a proxy.

Configure via env var:
    HERMES_ADAPTER_CORS_ORIGINS
        Comma-separated list of allowed origins, or ``*`` for any origin.
        Default: ``*`` (permissive — safe because this service binds to
        127.0.0.1 in local deployments and is typically already fronted
        by an authenticating proxy in hosted ones).

Example:
    HERMES_ADAPTER_CORS_ORIGINS=https://studio.example.com,https://app.akela.ai
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from aiohttp import web

_ALLOW_HEADERS = "Authorization, Content-Type, X-Requested-With"
_ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
_MAX_AGE = "86400"  # 24h preflight cache


def _allowed_origins() -> tuple[str, ...]:
    raw = os.environ.get("HERMES_ADAPTER_CORS_ORIGINS", "*").strip()
    if not raw or raw == "*":
        return ("*",)
    return tuple(o.strip() for o in raw.split(",") if o.strip())


def _pick_origin(request_origin: str | None, allowed: tuple[str, ...]) -> str | None:
    if not request_origin:
        return None
    if "*" in allowed:
        return request_origin  # echo the exact origin (required with credentials)
    if request_origin in allowed:
        return request_origin
    return None


@web.middleware
async def cors_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    origin_header = request.headers.get("Origin")
    allowed = _allowed_origins()

    # Preflight
    if request.method == "OPTIONS":
        origin = _pick_origin(origin_header, allowed)
        headers = {
            "Access-Control-Allow-Methods": _ALLOW_METHODS,
            "Access-Control-Allow-Headers": request.headers.get(
                "Access-Control-Request-Headers", _ALLOW_HEADERS
            ),
            "Access-Control-Max-Age": _MAX_AGE,
        }
        if origin:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        return web.Response(status=204, headers=headers)

    response = await handler(request)
    origin = _pick_origin(origin_header, allowed)
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        prev_vary = response.headers.get("Vary")
        response.headers["Vary"] = "Origin" if not prev_vary else f"{prev_vary}, Origin"
    return response
