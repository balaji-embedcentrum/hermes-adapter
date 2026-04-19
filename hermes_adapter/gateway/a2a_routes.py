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
from starlette.responses import JSONResponse, StreamingResponse

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


async def handle_jsonrpc(request: Request):
    """POST / — A2A JSON-RPC entry point.

    Accepted methods:
      tasks/send         — legacy one-shot (kept for older clients)
      message/send       — A2A v0.4.x one-shot JSON-RPC
      message/stream     — A2A v0.4.x streaming (SSE). Each event is a
                           JSON-RPC envelope whose ``result`` carries
                           either an ``artifact`` delta or a ``status``
                           transition. Akela's Hunt caller prefers this.
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

    if method not in ("tasks/send", "message/send", "message/stream"):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": rpc_id,
            },
            status_code=200,
        )

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
    task_id = params.get("id") or params.get("taskId") or "task-0"
    context_id = params.get("contextId") or session_id

    if method == "message/stream":
        return _stream_task(rpc_id, task_id, context_id, session_id, user_text)

    # Non-streaming path (tasks/send + message/send)
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


def _stream_task(
    rpc_id, task_id: str, context_id: str, session_id: str, user_text: str
) -> StreamingResponse:
    """Run the agent and emit A2A v0.4.x streaming SSE events.

    Emits, in order:
      1. ``status: working`` — task accepted
      2. N × ``artifact`` deltas with accumulated text
      3. Optional ``artifact`` deltas with ``data: {"type": "tool_call", "name": ...}``
      4. ``status: completed`` — terminal
    """
    import asyncio
    import json
    import queue

    from .openai_compat import _executor, _run_agent_sync

    async def _event_stream():
        # 1. working
        yield _sse_jsonrpc(rpc_id, {
            "taskId": task_id,
            "contextId": context_id,
            "status": {"state": "working"},
        })

        delta_q: queue.Queue = queue.Queue()

        def _on_delta(delta: str | None) -> None:
            if delta is not None:
                delta_q.put(delta)

        def _on_tool(name: str) -> None:
            delta_q.put({"type": "tool_call", "name": name})

        loop = asyncio.get_event_loop()
        try:
            agent_future = loop.run_in_executor(
                _executor,
                lambda: _run_agent_sync(
                    session_id,
                    user_text,
                    stream_delta_callback=_on_delta,
                ),
            )
        except RuntimeError as e:
            yield _sse_jsonrpc(rpc_id, {
                "taskId": task_id,
                "contextId": context_id,
                "status": {"state": "failed"},
                "error": str(e),
            })
            return

        accumulated = ""
        while not agent_future.done():
            try:
                chunk = await loop.run_in_executor(None, lambda: delta_q.get(timeout=0.05))
            except Exception:
                continue
            if isinstance(chunk, dict) and chunk.get("type") == "tool_call":
                yield _sse_jsonrpc(rpc_id, {
                    "taskId": task_id,
                    "contextId": context_id,
                    "artifact": {
                        "artifactId": f"tool_{chunk['name']}",
                        "parts": [{"kind": "data", "data": chunk}],
                    },
                })
            elif isinstance(chunk, str):
                accumulated += chunk
                yield _sse_jsonrpc(rpc_id, {
                    "taskId": task_id,
                    "contextId": context_id,
                    "artifact": {
                        "artifactId": "response",
                        "parts": [{"kind": "text", "text": accumulated}],
                    },
                })

        try:
            result, usage = await agent_future
        except RuntimeError as e:
            yield _sse_jsonrpc(rpc_id, {
                "taskId": task_id,
                "contextId": context_id,
                "status": {"state": "failed"},
                "error": str(e),
            })
            return

        # Drain any remaining deltas
        while True:
            try:
                chunk = delta_q.get_nowait()
            except queue.Empty:
                break
            if isinstance(chunk, str):
                accumulated += chunk

        # If the agent never emitted streaming deltas (non-streaming provider),
        # emit the full final_response as a single artifact.
        final = (result or {}).get("final_response") or ""
        if not accumulated and final:
            yield _sse_jsonrpc(rpc_id, {
                "taskId": task_id,
                "contextId": context_id,
                "artifact": {
                    "artifactId": "response",
                    "parts": [{"kind": "text", "text": final}],
                },
            })

        # Terminal status
        yield _sse_jsonrpc(rpc_id, {
            "taskId": task_id,
            "contextId": context_id,
            "status": {"state": "completed"},
            "metadata": {"usage": usage or {}},
        })

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_jsonrpc(rpc_id, result: dict) -> str:
    import json as _json

    envelope = {"jsonrpc": "2.0", "id": rpc_id, "result": result}
    return f"data: {_json.dumps(envelope)}\n\n"


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
