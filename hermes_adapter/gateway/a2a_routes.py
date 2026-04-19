"""A2A endpoints mounted into the gateway's Starlette app.

Serves the Agent Card at BOTH the v0.4.x path (``/.well-known/agent.json``)
and the v0.3.x legacy path (``/.well-known/agent-card.json``) so clients
like Akela that probe either one find us.

The JSON-RPC handler at ``POST /`` is delegated to the a2a-sdk's
``DefaultRequestHandler`` reusing the existing ``HermesAgentExecutor``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

try:
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.types import AgentCapabilities, AgentCard, AgentSkill
    _SDK_AVAILABLE = True
    _sdk_err = ""
except ImportError as _e:
    _SDK_AVAILABLE = False
    _sdk_err = str(_e)


def build_agent_card(port: int) -> dict:
    """Build a JSON-serializable Agent Card dict.

    Uses plain dicts rather than the a2a-sdk AgentCard type so we can always
    respond with it even if a2a-sdk isn't installed — clients can still
    discover us, they just can't invoke A2A JSON-RPC.
    """
    agent_name = os.getenv("AGENT_NAME", "hermes-agent")
    description = os.getenv("AGENT_DESCRIPTION", f"{agent_name} — hermes agent")
    skills_raw = [s.strip() for s in os.getenv("AGENT_SKILLS", "").split(",") if s.strip()]
    skills = [
        {
            "id": s.lower().replace(" ", "_"),
            "name": s,
            "description": f"{s} capability",
            "tags": [s.lower()],
        }
        for s in (skills_raw or [agent_name])
    ]
    public_url = os.getenv("A2A_PUBLIC_URL", f"http://localhost:{port}")

    return {
        "name": agent_name,
        "description": description,
        "url": public_url,
        "version": "1.0.0",
        "capabilities": {"streaming": True},
        "skills": skills,
        "default_input_modes": ["text"],
        "default_output_modes": ["text"],
    }


def make_agent_card_handler(port: int):
    """Return a Starlette handler that serves the Agent Card at a given path."""

    card = build_agent_card(port)

    async def handler(request: Request) -> JSONResponse:
        return JSONResponse(card)

    return handler


async def handle_jsonrpc(request: Request) -> JSONResponse:
    """POST / — A2A JSON-RPC entry point.

    We re-implement a tiny surface here rather than depending on a2a-sdk's
    A2AStarletteApplication internal routing. Accepted methods:
      tasks/send                 — one-shot task execution
      message/send               — A2A v0.4 equivalent

    Streaming (``tasks/sendSubscribe``) is NOT served over this JSON-RPC
    endpoint — clients that want streaming should use SSE via the OpenAI
    ``/v1/chat/completions`` path. Akela's local-chat uses A2A over
    non-streaming JSON-RPC for its first probe, which is what this serves.
    """
    try:
        req = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Invalid JSON"}, "id": None},
            status_code=400,
        )

    rpc_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if method not in ("tasks/send", "message/send"):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": rpc_id,
            },
            status_code=200,
        )

    # Extract user message from either "message" (v0.4) or params directly (older)
    message = params.get("message") or params
    user_text = _extract_text(message)
    if not user_text:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32602, "message": "No text content in message"},
                "id": rpc_id,
            },
            status_code=200,
        )

    session_id = params.get("sessionId") or params.get("contextId") or "default"

    # Run the agent synchronously (non-streaming JSON-RPC)
    from .openai_compat import _run_agent_async

    try:
        result, _ = await _run_agent_async(session_id, user_text)
    except RuntimeError as e:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": str(e)},
                "id": rpc_id,
            },
            status_code=200,
        )

    final = result.get("final_response") or result.get("error") or ""
    task_id = params.get("id") or params.get("taskId") or "task-0"
    context_id = params.get("contextId") or session_id

    # A2A-style response with a single text artifact.
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "taskId": task_id,
                "contextId": context_id,
                "status": {"state": "completed"},
                "artifacts": [
                    {
                        "artifactId": "response",
                        "parts": [{"type": "text", "text": final}],
                    }
                ],
            },
        }
    )


def _extract_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    parts = message.get("parts") or []
    out: list[str] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text" or p.get("kind") == "text":
            out.append(p.get("text", ""))
    return " ".join(out).strip()
