"""Lazy bridge to hermes-agent's AIAgent.

Centralizes the ``_make_agent`` helper so that both the OpenAI-compatible
``/v1/chat/completions`` handler and the A2A executor reuse the same
provider resolution logic.

hermes-agent is imported lazily — the module is safe to import without
hermes-agent installed (the error surfaces only when an agent is actually
requested).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def make_agent(
    session_id: str,
    stream_delta_callback=None,
    tool_gen_callback=None,
) -> Any:
    """Create a Hermes AIAgent using the runtime provider from config.

    Raises RuntimeError if hermes-agent isn't importable.
    """
    try:
        from run_agent import AIAgent  # type: ignore[import-not-found]
        from hermes_cli.config import load_config  # type: ignore[import-not-found]
        from hermes_cli.runtime_provider import resolve_runtime_provider  # type: ignore[import-not-found]
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

    toolsets_env = os.getenv("A2A_TOOLSETS", "").strip()
    enabled_toolsets = (
        [s.strip() for s in toolsets_env.split(",") if s.strip()] if toolsets_env else None
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
        logger.debug("falling back to default provider resolution", exc_info=True)

    return AIAgent(**kwargs)


def current_model() -> str:
    """Return the configured model string for this agent's HERMES_HOME."""
    try:
        from hermes_cli.config import load_config  # type: ignore[import-not-found]

        model_cfg = load_config().get("model")
        if isinstance(model_cfg, dict):
            return str(model_cfg.get("default") or "unknown")
        if isinstance(model_cfg, str):
            return model_cfg
    except Exception:
        pass
    return os.getenv("AGENT_MODEL", "unknown")
