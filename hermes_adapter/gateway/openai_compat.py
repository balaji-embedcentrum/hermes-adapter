"""OpenAI-compatible ``/v1/*`` endpoints.

These are the routes Hermes Studio (and Akela's OpenAI fallback) hit:
    GET  /v1/models                 list one model (this agent's)
    POST /v1/chat/completions       SSE streaming chat that wraps hermes-agent

The chat handler spawns a HermesAgent via the lazy bridge, streams deltas
through a queue, and re-emits each delta as an OpenAI chat completion chunk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from .hermes_bridge import current_model, make_agent

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gateway-openai")


async def handle_models(request: Request) -> JSONResponse:
    """GET /v1/models — OpenAI-style model list with this agent's model."""
    model = current_model()
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": model,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": os.getenv("AGENT_NAME", "hermes-agent"),
                }
            ],
        }
    )


def _extract_user_message(messages: list[dict]) -> str:
    """Collapse an OpenAI-style `messages` array into a single user prompt.

    We concatenate all `user` and `system` messages in order; anything else
    is ignored (the hermes agent maintains its own conversation state via
    session_id).
    """
    parts: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if isinstance(content, list):
            # Multimodal content array — pull out text parts only
            texts = [p.get("text", "") for p in content if p.get("type") == "text"]
            content = " ".join(texts)
        if role in ("system", "user") and content.strip():
            parts.append(content.strip())
    return "\n\n".join(parts)


def _make_chunk(delta: str, model: str, completion_id: str, finish_reason: str | None = None) -> dict:
    """Build one OpenAI chat-completion SSE chunk."""
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": ({"content": delta} if delta else {}),
                "finish_reason": finish_reason,
            }
        ],
    }


async def handle_chat_completions(request: Request) -> StreamingResponse | JSONResponse:
    """POST /v1/chat/completions — streaming OpenAI-compatible chat."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "Invalid JSON body"}}, status_code=400)

    messages = body.get("messages") or []
    if not messages:
        return JSONResponse(
            {"error": {"message": "messages is required"}}, status_code=400
        )

    user_message = _extract_user_message(messages)
    if not user_message:
        return JSONResponse(
            {"error": {"message": "no user/system text content in messages"}}, status_code=400
        )

    stream = bool(body.get("stream", False))
    model = body.get("model") or current_model()
    session_id = body.get("user") or request.headers.get("x-hermes-session-id") or str(uuid.uuid4())[:8]
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    if not stream:
        # Non-streaming: run to completion, return one JSON response.
        try:
            result, _ = await _run_agent_async(session_id, user_message)
        except RuntimeError as e:
            return JSONResponse({"error": {"message": str(e)}}, status_code=500)

        text = result.get("final_response") or result.get("error") or ""
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    async def _event_stream():
        delta_q: queue.Queue = queue.Queue()

        def _on_delta(delta: str | None) -> None:
            if delta is not None:
                delta_q.put(delta)

        loop = asyncio.get_event_loop()
        try:
            agent_future = loop.run_in_executor(
                _executor,
                lambda: _run_agent_sync(session_id, user_message, stream_delta_callback=_on_delta),
            )
        except RuntimeError as e:
            # hermes-agent missing or similar
            yield _sse(_make_chunk(f"[error] {e}", model, completion_id, finish_reason="stop"))
            yield "data: [DONE]\n\n"
            return

        while not agent_future.done():
            try:
                chunk = await loop.run_in_executor(None, lambda: delta_q.get(timeout=0.05))
            except Exception:
                continue  # queue.Empty
            if isinstance(chunk, str):
                yield _sse(_make_chunk(chunk, model, completion_id))

        try:
            result, _ = await agent_future
        except RuntimeError as e:
            yield _sse(_make_chunk(f"\n\n[error] {e}", model, completion_id, finish_reason="stop"))
            yield "data: [DONE]\n\n"
            return

        # Drain any deltas that arrived after the watchdog exited
        while True:
            try:
                chunk = delta_q.get_nowait()
            except queue.Empty:
                break
            if isinstance(chunk, str):
                yield _sse(_make_chunk(chunk, model, completion_id))

        # If the agent produced no streamed deltas (e.g. non-streaming provider),
        # still emit a final_response chunk so the client sees content.
        final = (result or {}).get("final_response", "")
        err = (result or {}).get("error")
        if err and not final:
            yield _sse(_make_chunk(f"[error] {err}", model, completion_id))

        yield _sse(_make_chunk("", model, completion_id, finish_reason="stop"))
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _run_agent_sync(
    session_id: str, user_message: str, stream_delta_callback=None
) -> tuple[dict, dict]:
    agent = make_agent(session_id=session_id, stream_delta_callback=stream_delta_callback)
    result = agent.run_conversation(user_message=user_message, conversation_history=[])
    usage = {
        "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
        "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
    }
    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return result, usage


async def _run_agent_async(session_id: str, user_message: str) -> tuple[dict, dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor, lambda: _run_agent_sync(session_id, user_message)
    )
