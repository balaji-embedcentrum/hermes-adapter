"""A2A client — discover, call, and scan remote A2A agents over raw HTTP + JSON-RPC.

This module depends only on ``httpx`` — no hermes-agent, no a2a-sdk. It can be
used standalone from any Python program that wants to talk to A2A endpoints.

Public API:
    a2a_discover(url)                        Fetch the Agent Card
    a2a_call(url, message, ...)              Send a task (SSE streaming when available)
    a2a_local_scan(host, port_start, ...)    Probe localhost ports for A2A agents

Hermes tool integration:
    ``register_hermes_tools(registry)`` registers the three tools into a hermes-agent
    ``tools.registry``. Call it from a hermes-agent plugin if you want the remote-agent
    tools exposed inside a local Hermes.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 120.0  # seconds per A2A call

_CARD_CACHE: Dict[str, Dict] = {}
_CARD_TTL = 300.0  # 5 minutes


def _get_cached_card(url: str) -> Optional[Dict]:
    entry = _CARD_CACHE.get(url)
    if entry and time.monotonic() < entry["expires"]:
        return entry["card"]
    return None


def _set_cached_card(url: str, card: Dict) -> None:
    _CARD_CACHE[url] = {"card": card, "expires": time.monotonic() + _CARD_TTL}


def _load_a2a_agents_from_hermes() -> Dict[str, Dict[str, str]]:
    """Load ``a2a_agents`` from hermes-agent's config if available. Otherwise {}."""
    try:
        from hermes_cli.config import load_config  # type: ignore[import-not-found]
        cfg = load_config()
        agents = cfg.get("a2a_agents") or {}
        if isinstance(agents, dict):
            return agents
    except Exception:
        logger.debug("Could not load a2a_agents from hermes config", exc_info=True)
    return {}


def _fetch_agent_card(url: str) -> Dict:
    cached = _get_cached_card(url)
    if cached is not None:
        return cached

    card_url = f"{url}/.well-known/agent.json"
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(card_url)
        resp.raise_for_status()
        card = resp.json()

    _set_cached_card(url, card)
    return card


def a2a_discover(url: str) -> str:
    """Fetch the Agent Card from an A2A endpoint.

    Returns a JSON string describing the agent (name, description, skills, model,
    streaming support). Results are cached for 5 minutes.
    """
    url = url.rstrip("/")
    try:
        card = _fetch_agent_card(url)
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"})
    except Exception as e:
        return json.dumps({"error": f"Could not reach {url}/.well-known/agent.json: {e}"})

    name = card.get("name", "unknown")
    description = card.get("description", "")
    skills = [s.get("name", "") for s in (card.get("skills") or [])]
    model = (card.get("metadata") or {}).get("model", "")
    streaming = (card.get("capabilities") or {}).get("streaming", False)

    return json.dumps(
        {
            "name": name,
            "description": description,
            "skills": skills,
            "model": model,
            "streaming": streaming,
            "endpoint": url,
            "raw": card,
        },
        ensure_ascii=False,
        indent=2,
    )


def _extract_artifacts_text(result: Dict) -> str:
    texts: list[str] = []
    for artifact in (result.get("artifacts") or []):
        for part in (artifact.get("parts") or []):
            if part.get("type") == "text" and part.get("text"):
                texts.append(part["text"])
    return "\n".join(texts).strip()


def _call_streaming(url: str, payload: Dict, headers: Dict) -> str:
    """Call a remote A2A agent via ``tasks/sendSubscribe`` (SSE)."""
    payload = dict(payload, method="tasks/sendSubscribe")
    accumulated = ""

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            with client.stream("POST", url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                for raw_line in resp.iter_lines():
                    if not raw_line.startswith("data:"):
                        continue
                    data_str = raw_line[len("data:"):].strip()
                    if not data_str:
                        continue
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    result = event.get("result") or {}
                    chunk = _extract_artifacts_text(result)
                    if chunk:
                        accumulated = chunk  # cumulative

                    state = (result.get("status") or {}).get("state", "")
                    if state in ("completed", "failed", "canceled"):
                        break
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text[:300]}"})
    except Exception as e:
        return json.dumps({"error": f"Streaming request failed: {e}"})

    if not accumulated:
        return json.dumps({"error": "Agent returned an empty streaming response"})
    return accumulated


def a2a_call(
    url: str,
    message: str,
    session_id: Optional[str] = None,
    bearer_token: Optional[str] = None,
    stream: Optional[bool] = None,
) -> str:
    """Send a task to a remote A2A agent and return its response.

    Auto-detects streaming from the agent's Agent Card unless ``stream`` is given.
    """
    url = url.rstrip("/")
    task_id = str(uuid.uuid4())[:8]
    ctx_id = session_id or str(uuid.uuid4())[:8]

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    if stream is None:
        try:
            card = _fetch_agent_card(url)
            stream = bool((card.get("capabilities") or {}).get("streaming", False))
        except Exception:
            stream = False

    payload = {
        "jsonrpc": "2.0",
        "id": task_id,
        "method": "tasks/send",
        "params": {
            "id": task_id,
            "sessionId": ctx_id,
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": message}],
            },
        },
    }

    if stream:
        return _call_streaming(url, payload, headers)

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text[:300]}"})
    except Exception as e:
        return json.dumps({"error": f"Request failed: {e}"})

    if "error" in data:
        err = data["error"]
        return json.dumps({"error": err.get("message", str(err))})

    result = data.get("result") or {}
    response_text = _extract_artifacts_text(result)
    if not response_text:
        return json.dumps({"error": "Agent returned an empty response", "raw": result})
    return response_text


_SCAN_TIMEOUT = 2.0
_DEFAULT_SCAN_START = 9000
_DEFAULT_SCAN_END = 9010


def a2a_local_scan(
    host: str = "localhost",
    port_start: int = _DEFAULT_SCAN_START,
    port_end: int = _DEFAULT_SCAN_END,
) -> str:
    """Scan ``host`` ports [port_start, port_end] for A2A agents (via Agent Card)."""
    found = []
    with httpx.Client(timeout=_SCAN_TIMEOUT) as client:
        for port in range(port_start, port_end + 1):
            url = f"http://{host}:{port}"
            card_url = f"{url}/.well-known/agent.json"
            try:
                resp = client.get(card_url)
                if resp.status_code == 200:
                    card = resp.json()
                    found.append(
                        {
                            "endpoint": url,
                            "name": card.get("name", "unknown"),
                            "description": card.get("description", ""),
                            "skills": [s.get("name", "") for s in (card.get("skills") or [])],
                            "streaming": (card.get("capabilities") or {}).get("streaming", False),
                        }
                    )
            except Exception:
                pass

    if not found:
        return json.dumps(
            {
                "found": 0,
                "message": f"No A2A agents found on {host} ports {port_start}-{port_end}",
                "agents": [],
            }
        )
    return json.dumps({"found": len(found), "agents": found}, ensure_ascii=False, indent=2)


A2A_DISCOVER_SCHEMA = {
    "name": "a2a_discover",
    "description": (
        "Fetch the Agent Card from any A2A-compatible agent endpoint. "
        "Returns the agent's name, description, skills, model, and capabilities. "
        "Use this to learn what a remote agent can do before calling it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Base URL of the A2A agent (e.g. http://192.168.1.100:9000)",
            }
        },
        "required": ["url"],
    },
}


def _build_call_description() -> str:
    agents = _load_a2a_agents_from_hermes()
    base = (
        "Send a task to a remote A2A agent and get its response. "
        "Supports any agent that implements the Google A2A protocol "
        "(Hermes, LangChain, CrewAI, AutoGen, Vertex AI agents, etc.).\n\n"
        "Automatically uses SSE streaming when the agent supports it (detected from "
        "its Agent Card). Override with stream=true/false if needed.\n\n"
        "Provide either 'url' (direct endpoint) or 'agent_name' (from config).\n"
    )
    if agents:
        names = ", ".join(
            f"{name} — {cfg.get('description', cfg.get('url', ''))}" for name, cfg in agents.items()
        )
        base += f"\nConfigured agents: {names}"
    return base


A2A_CALL_SCHEMA = {
    "name": "a2a_call",
    "description": _build_call_description(),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Direct URL of the A2A agent. Required if agent_name is not provided.",
            },
            "agent_name": {
                "type": "string",
                "description": "Name of a pre-configured agent from config.yaml a2a_agents section.",
            },
            "message": {
                "type": "string",
                "description": "The task or question to send to the remote agent.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID for multi-turn conversation.",
            },
            "bearer_token": {
                "type": "string",
                "description": "Optional Bearer token for authentication.",
            },
            "stream": {
                "type": "boolean",
                "description": "Force SSE streaming (tasks/sendSubscribe). Omit to auto-detect.",
            },
        },
        "required": ["message"],
    },
}


A2A_LOCAL_SCAN_SCHEMA = {
    "name": "a2a_local_scan",
    "description": (
        "Scan localhost ports to discover running A2A agents. "
        "Probes each port for a /.well-known/agent.json endpoint. "
        f"Default scan range: ports {_DEFAULT_SCAN_START}-{_DEFAULT_SCAN_END}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Host to scan (default: localhost)"},
            "port_start": {"type": "integer", "description": f"First port (default: {_DEFAULT_SCAN_START})"},
            "port_end": {"type": "integer", "description": f"Last port inclusive (default: {_DEFAULT_SCAN_END})"},
        },
        "required": [],
    },
}


def _tool_a2a_discover(args: Dict[str, Any], **_kw) -> str:
    url = args.get("url", "").strip()
    if not url:
        return json.dumps({"error": "url is required"})
    return a2a_discover(url)


def _tool_a2a_call(args: Dict[str, Any], **_kw) -> str:
    url = args.get("url", "").strip()
    message = args.get("message", "").strip()
    session_id = args.get("session_id")
    bearer_token = args.get("bearer_token")
    stream = args.get("stream")

    if not url and args.get("agent_name"):
        agents = _load_a2a_agents_from_hermes()
        agent_cfg = agents.get(args["agent_name"])
        if agent_cfg:
            url = agent_cfg.get("url", "")
            if not bearer_token:
                bearer_token = agent_cfg.get("bearer_token")
        else:
            return json.dumps(
                {
                    "error": f"Agent '{args['agent_name']}' not found in config.yaml a2a_agents.",
                    "available_agents": list(agents.keys()),
                }
            )

    if not url:
        return json.dumps({"error": "Provide 'url' or 'agent_name'"})
    if not message:
        return json.dumps({"error": "message is required"})

    return a2a_call(url, message, session_id=session_id, bearer_token=bearer_token, stream=stream)


def _tool_a2a_local_scan(args: Dict[str, Any], **_kw) -> str:
    host = args.get("host", "localhost")
    port_start = int(args.get("port_start", _DEFAULT_SCAN_START))
    port_end = int(args.get("port_end", _DEFAULT_SCAN_END))
    if port_start > port_end:
        return json.dumps({"error": "port_start must be <= port_end"})
    if port_end - port_start > 100:
        return json.dumps({"error": "Scan range too large (max 100 ports)"})
    return a2a_local_scan(host=host, port_start=port_start, port_end=port_end)


def register_hermes_tools(registry: Any) -> None:
    """Register the three A2A tools with a hermes-agent ``tools.registry``.

    Call this from a hermes-agent plugin to expose remote-agent capabilities
    inside a local Hermes session. Safe to call multiple times (registry
    handles dedup).
    """
    registry.register(
        name="a2a_discover",
        toolset="a2a",
        schema=A2A_DISCOVER_SCHEMA,
        handler=_tool_a2a_discover,
        check_fn=lambda: True,
        emoji="🔍",
    )
    registry.register(
        name="a2a_call",
        toolset="a2a",
        schema=A2A_CALL_SCHEMA,
        handler=_tool_a2a_call,
        check_fn=lambda: True,
        emoji="🤝",
    )
    registry.register(
        name="a2a_local_scan",
        toolset="a2a",
        schema=A2A_LOCAL_SCAN_SCHEMA,
        handler=_tool_a2a_local_scan,
        check_fn=lambda: True,
        emoji="📡",
    )
