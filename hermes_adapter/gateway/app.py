"""Build the unified Starlette app served on one port per agent."""

from __future__ import annotations

import logging
import os
import sys

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import a2a_routes, openai_compat, workspace_routes

logger = logging.getLogger(__name__)

_HEALTH_ROUTES = ("/health", "/v1/health")


async def handle_health(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "hermes-adapter-gateway",
            "agent": os.getenv("AGENT_NAME", "hermes-agent"),
        }
    )


def _cors_origins() -> list[str]:
    raw = os.environ.get("HERMES_ADAPTER_CORS_ORIGINS", "*").strip()
    if not raw or raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def build_app(port: int | None = None) -> Starlette:
    """Build the per-agent gateway Starlette app.

    ``port`` is used only to synthesize the default ``A2A_PUBLIC_URL`` in
    the Agent Card when the env var isn't set.
    """
    port = port or int(os.getenv("A2A_PORT", "9001"))

    routes: list[Route] = []

    for path in _HEALTH_ROUTES:
        routes.append(Route(path, handle_health, methods=["GET"]))

    # OpenAI-compatible
    routes.append(Route("/v1/models", openai_compat.handle_models, methods=["GET"]))
    routes.append(
        Route("/v1/chat/completions", openai_compat.handle_chat_completions, methods=["POST"])
    )

    # A2A
    card_handler = a2a_routes.make_agent_card_handler(port)
    routes.append(Route("/.well-known/agent.json", card_handler, methods=["GET"]))
    routes.append(Route("/.well-known/agent-card.json", card_handler, methods=["GET"]))
    routes.append(Route("/", a2a_routes.handle_jsonrpc, methods=["POST"]))

    # Workspace (ported to Starlette)
    routes.extend(
        [
            Route("/ws", workspace_routes.handle_list, methods=["GET"]),
            Route("/ws/activate", workspace_routes.handle_activate, methods=["POST"]),
            Route("/ws/deactivate", workspace_routes.handle_deactivate, methods=["POST"]),
            Route("/ws/{repo}/init", workspace_routes.handle_init, methods=["POST"]),
            Route("/ws/{repo}/tree", workspace_routes.handle_tree, methods=["GET"]),
            Route("/ws/{repo}/file", workspace_routes.handle_file_get, methods=["GET"]),
            Route("/ws/{repo}/file", workspace_routes.handle_file_post, methods=["POST"]),
            Route("/ws/{repo}/file", workspace_routes.handle_file_delete, methods=["DELETE"]),
            Route("/ws/{repo}/git/status", workspace_routes.handle_git_status, methods=["GET"]),
            Route("/ws/{repo}/git/commit", workspace_routes.handle_git_commit, methods=["POST"]),
            Route("/ws/{repo}/git/push", workspace_routes.handle_git_push, methods=["POST"]),
            Route("/ws/{repo}/git/pull", workspace_routes.handle_git_pull, methods=["POST"]),
            Route("/ws/{repo}/git/pr", workspace_routes.handle_git_pr, methods=["POST"]),
            Route("/ws/{repo}/git/log", workspace_routes.handle_git_log, methods=["GET"]),
            Route("/ws/{repo}/git/files", workspace_routes.handle_git_files, methods=["GET"]),
            Route("/ws/{repo}/git/diff", workspace_routes.handle_git_diff, methods=["GET"]),
            Route("/ws/{repo}/git/branches", workspace_routes.handle_git_branches, methods=["GET"]),
            Route("/ws/{repo}/git/show/{sha}", workspace_routes.handle_git_show, methods=["GET"]),
            Route("/ws/{repo}/symbols", workspace_routes.handle_symbols, methods=["GET"]),
            Route(
                "/ws/{repo}/symbols/invalidate",
                workspace_routes.handle_symbols_invalidate,
                methods=["POST"],
            ),
        ]
    )

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=_cors_origins(),
            allow_origin_regex=None,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            allow_credentials=False,
            expose_headers=["*"],
            max_age=86400,
        )
    ]

    return Starlette(routes=routes, middleware=middleware)


def run(host: str | None = None, port: int | None = None) -> None:
    """Serve the gateway with uvicorn (blocking)."""
    try:
        import uvicorn
    except ImportError as e:
        raise RuntimeError(
            "uvicorn is not installed. Install with: pip install 'hermes-adapter[a2a]'"
        ) from e

    host = host or os.getenv("A2A_HOST", "0.0.0.0")
    port = port or int(os.getenv("A2A_PORT", "9001"))

    # If the user has hermes-agent source on disk (not pip-installed), let them
    # add it via env var.
    hermes_root = os.getenv("HERMES_AGENT_ROOT")
    if hermes_root and hermes_root not in sys.path:
        sys.path.insert(0, hermes_root)

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            stream=sys.stderr,
        )
    logger.info(
        "gateway listening on http://%s:%d  (agent=%s)",
        host,
        port,
        os.getenv("AGENT_NAME", "hermes-agent"),
    )

    app = build_app(port=port)
    uvicorn.run(app, host=host, port=port, log_level="warning")
