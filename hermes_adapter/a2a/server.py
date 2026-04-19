"""A2A (Agent-to-Agent) server — exposes hermes-agent via the Google A2A protocol.

Uses the official ``a2a-sdk`` (``pip install 'a2a-sdk[http-server]'``) to build
a standards-compliant Starlette/FastAPI application that any A2A orchestrator
can discover and call — Vertex AI Agent Engine, LangGraph, Akela, etc.

Exposes (via a2a-sdk):
    GET  /.well-known/agent.json    Agent Card
    POST /                           JSON-RPC 2.0 (tasks/send, tasks/sendSubscribe)

Configure via environment variables:
    A2A_HOST, A2A_PORT, A2A_KEY, A2A_PUBLIC_URL
    AGENT_NAME, AGENT_DESCRIPTION, AGENT_SKILLS, AGENT_MODEL
    A2A_TOOLSETS      comma-separated toolset filter for the Hermes AIAgent

The Hermes ``AIAgent`` is imported lazily so this module can be imported
without hermes-agent installed (e.g. when only the ``client`` helpers are
needed). The agent is only required at request-handling time.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="a2a-agent")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9000


try:
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.events import EventQueue
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.server.apps import A2AStarletteApplication
    from a2a.types import (
        AgentCard,
        AgentCapabilities,
        AgentSkill,
        TaskArtifactUpdateEvent,
        TaskState,
        TaskStatus,
        TaskStatusUpdateEvent,
    )
    _SDK_AVAILABLE = True
    _sdk_err_msg = ""
except ImportError as _sdk_err:
    _SDK_AVAILABLE = False
    _sdk_err_msg = str(_sdk_err)
    AgentExecutor = object  # type: ignore[assignment,misc]


def _make_agent(
    session_id: str,
    stream_delta_callback=None,
    tool_gen_callback=None,
) -> Any:
    """Create a Hermes AIAgent using the runtime provider from config.

    Raises RuntimeError if hermes-agent is not importable.
    """
    try:
        from run_agent import AIAgent
        from hermes_cli.config import load_config
        from hermes_cli.runtime_provider import resolve_runtime_provider
    except ImportError as e:
        raise RuntimeError(
            f"hermes-agent is not importable ({e}). "
            "Either pip-install hermes-agent, or set HERMES_AGENT_ROOT to its source checkout."
        ) from e

    config = load_config()
    model_cfg = config.get("model")
    default_model = "anthropic/claude-opus-4.6"
    config_provider = None

    if isinstance(model_cfg, dict):
        default_model = str(model_cfg.get("default") or default_model)
        config_provider = model_cfg.get("provider")
    elif isinstance(model_cfg, str) and model_cfg.strip():
        default_model = model_cfg.strip()

    # Toolset filter — default to ALL toolsets (same as ``hermes chat``).
    # Set A2A_TOOLSETS=tool1,tool2 to restrict.
    toolsets_env = os.getenv("A2A_TOOLSETS", "").strip()
    enabled_toolsets = (
        [s.strip() for s in toolsets_env.split(",") if s.strip()]
        if toolsets_env
        else None
    )

    kwargs: dict[str, Any] = {
        "platform": "a2a",
        "quiet_mode": True,
        "session_id": session_id,
        "model": default_model,
    }
    if enabled_toolsets:
        kwargs["enabled_toolsets"] = enabled_toolsets
    if stream_delta_callback is not None:
        kwargs["stream_delta_callback"] = stream_delta_callback
    if tool_gen_callback is not None:
        kwargs["tool_gen_callback"] = tool_gen_callback

    try:
        runtime = resolve_runtime_provider(requested=config_provider)
        kwargs.update(
            {
                "provider": runtime.get("provider"),
                "api_mode": runtime.get("api_mode"),
                "base_url": runtime.get("base_url"),
                "api_key": runtime.get("api_key"),
                "command": runtime.get("command"),
                "args": list(runtime.get("args") or []),
            }
        )
    except Exception:
        logger.debug("A2A falling back to default provider resolution", exc_info=True)

    return AIAgent(**kwargs)


def build_agent_card(port: int) -> "AgentCard":
    """Build the A2A Agent Card from env vars."""
    if not _SDK_AVAILABLE:
        raise RuntimeError(f"a2a-sdk is not installed: {_sdk_err_msg}")

    agent_name = os.getenv("AGENT_NAME", "hermes-agent")
    skills_raw = [s.strip() for s in os.getenv("AGENT_SKILLS", "").split(",") if s.strip()]
    skills = [
        AgentSkill(
            id=s.lower().replace(" ", "_"),
            name=s,
            description=f"{s} capability",
            tags=[s.lower()],
        )
        for s in (skills_raw or [agent_name])
    ]
    public_url = os.getenv("A2A_PUBLIC_URL", f"http://localhost:{port}")

    return AgentCard(
        name=agent_name,
        description=os.getenv("AGENT_DESCRIPTION", f"{agent_name} — Hermes agent"),
        url=public_url,
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        skills=skills,
        default_input_modes=["text"],
        default_output_modes=["text"],
    )


class HermesAgentExecutor(AgentExecutor):  # type: ignore[misc,valid-type]
    """A2A AgentExecutor that delegates to a Hermes AIAgent."""

    async def execute(self, context: "RequestContext", event_queue: "EventQueue") -> None:
        task_id = context.task_id
        context_id = getattr(context, "context_id", None) or task_id
        user_message = _extract_message_content(context.message)
        session_id = context_id

        if not user_message:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    taskId=task_id,
                    contextId=context_id,
                    status=TaskStatus(state=TaskState.failed),
                    final=True,
                )
            )
            return

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                taskId=task_id,
                contextId=context_id,
                status=TaskStatus(state=TaskState.working),
                final=False,
            )
        )

        accumulated = ""
        loop = asyncio.get_event_loop()

        import queue as _q
        delta_queue: _q.Queue = _q.Queue()

        def _on_delta(delta: str | None) -> None:
            if delta is not None:
                delta_queue.put(delta)

        def _on_tool_gen(tool_name: str) -> None:
            delta_queue.put({"type": "tool_call", "name": tool_name})

        agent_future = loop.run_in_executor(
            _executor,
            lambda: _run_sync(
                session_id,
                user_message,
                stream_delta_callback=_on_delta,
                tool_gen_callback=_on_tool_gen,
            ),
        )

        while not agent_future.done():
            try:
                token = await loop.run_in_executor(
                    None, lambda: delta_queue.get(timeout=0.05)
                )
                if isinstance(token, dict) and token.get("type") == "tool_call":
                    await event_queue.enqueue_event(
                        TaskArtifactUpdateEvent(
                            taskId=task_id,
                            contextId=context_id,
                            artifact={
                                "artifactId": f"tool_{token['name']}",
                                "parts": [
                                    {
                                        "kind": "data",
                                        "data": {"type": "tool_call", "name": token["name"]},
                                    }
                                ],
                            },
                            final=False,
                        )
                    )
                elif isinstance(token, str):
                    accumulated += token
                    await event_queue.enqueue_event(
                        TaskArtifactUpdateEvent(
                            taskId=task_id,
                            contextId=context_id,
                            artifact={
                                "artifactId": "response",
                                "parts": [{"type": "text", "text": accumulated}],
                            },
                            final=False,
                        )
                    )
            except Exception:
                pass  # queue.Empty — keep polling

        result, _ = await agent_future
        final_text = result.get("final_response", "") or accumulated or result.get("error", "")

        if final_text and not accumulated:
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    taskId=task_id,
                    contextId=context_id,
                    artifact={
                        "artifactId": "response",
                        "parts": [{"type": "text", "text": final_text}],
                    },
                    final=True,
                )
            )

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                taskId=task_id,
                contextId=context_id,
                status=TaskStatus(state=TaskState.completed),
                final=True,
            )
        )

    async def cancel(self, context: "RequestContext", event_queue: "EventQueue") -> None:
        context_id = getattr(context, "context_id", None) or context.task_id
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                taskId=context.task_id,
                contextId=context_id,
                status=TaskStatus(state=TaskState.canceled),
                final=True,
            )
        )


def build_app(port: int | None = None) -> Any:
    """Build and return the A2A Starlette application.

    Raises RuntimeError if a2a-sdk is not installed.
    """
    if not _SDK_AVAILABLE:
        raise RuntimeError(
            f"a2a-sdk is not installed: {_sdk_err_msg}\n"
            "Install it with: pip install 'hermes-adapter[a2a]'"
        )

    if port is None:
        port = int(os.getenv("A2A_PORT", str(DEFAULT_PORT)))

    agent_card = build_agent_card(port)
    handler = DefaultRequestHandler(
        agent_executor=HermesAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    app = A2AStarletteApplication(agent_card=agent_card, http_handler=handler).build()

    # Browsers calling localhost from a different origin need permissive CORS.
    from starlette.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


def _extract_message_content(message: Any):
    """Extract content from an A2A Message.

    Returns a plain string for text-only messages, or a list of Anthropic-format
    content blocks (text + image) when image file parts are present.
    """
    if message is None:
        return ""

    parts = getattr(message, "parts", None) or []
    texts: list[str] = []
    image_blocks: list[dict] = []

    for part in parts:
        if hasattr(part, "text"):
            texts.append(str(part.text))
        elif hasattr(part, "root") and hasattr(part.root, "text"):
            texts.append(str(part.root.text))
        elif isinstance(part, dict) and (part.get("type") == "text" or part.get("kind") == "text"):
            texts.append(part.get("text", ""))
        elif isinstance(part, dict) and part.get("kind") == "file":
            file_info = part.get("file") or {}
            mime = file_info.get("mimeType", "")
            b64 = file_info.get("bytes", "")
            if mime.startswith("image/") and b64:
                image_blocks.append(
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
                )
        elif hasattr(part, "root") and hasattr(part.root, "file"):
            file_info = part.root.file
            mime = getattr(file_info, "mimeType", "") or ""
            b64 = getattr(file_info, "bytes", "") or ""
            if mime.startswith("image/") and b64:
                image_blocks.append(
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
                )

    text = " ".join(texts).strip()
    if not image_blocks:
        return text

    content: list[dict] = list(image_blocks)
    if text:
        content.append({"type": "text", "text": text})
    return content


def _run_sync(
    session_id: str,
    user_message: str,
    stream_delta_callback=None,
    tool_gen_callback=None,
) -> tuple[dict, dict]:
    """Synchronous Hermes invocation (runs in thread executor)."""
    agent = _make_agent(
        session_id=session_id,
        stream_delta_callback=stream_delta_callback,
        tool_gen_callback=tool_gen_callback,
    )
    result = agent.run_conversation(user_message=user_message, conversation_history=[])
    usage = {
        "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
        "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
        "total_tokens": (
            (getattr(agent, "session_prompt_tokens", 0) or 0)
            + (getattr(agent, "session_completion_tokens", 0) or 0)
        ),
    }
    return result, usage
