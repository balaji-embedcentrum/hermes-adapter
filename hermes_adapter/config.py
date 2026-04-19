"""Environment-driven configuration for hermes-adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterConfig:
    workspace_root: str
    workspace_host: str
    workspace_port: int
    a2a_host: str
    a2a_port: int
    a2a_key: str | None
    a2a_public_url: str | None
    agent_name: str
    agent_description: str
    agent_skills: tuple[str, ...]
    agent_model: str
    a2a_toolsets: tuple[str, ...]


def load() -> AdapterConfig:
    """Load adapter config from environment variables."""
    a2a_port = int(os.getenv("A2A_PORT", "9000"))
    skills_raw = os.getenv("AGENT_SKILLS", "")
    toolsets_raw = os.getenv("A2A_TOOLSETS", "")
    return AdapterConfig(
        workspace_root=os.getenv("HERMES_WORKSPACE_DIR", "/workspaces"),
        workspace_host=os.getenv("HERMES_ADAPTER_HOST", "0.0.0.0"),
        workspace_port=int(os.getenv("HERMES_ADAPTER_PORT", "8766")),
        a2a_host=os.getenv("A2A_HOST", "0.0.0.0"),
        a2a_port=a2a_port,
        a2a_key=os.getenv("A2A_KEY") or None,
        a2a_public_url=os.getenv("A2A_PUBLIC_URL") or None,
        agent_name=os.getenv("AGENT_NAME", "hermes-agent"),
        agent_description=os.getenv("AGENT_DESCRIPTION", ""),
        agent_skills=tuple(s.strip() for s in skills_raw.split(",") if s.strip()),
        agent_model=os.getenv("AGENT_MODEL", ""),
        a2a_toolsets=tuple(s.strip() for s in toolsets_raw.split(",") if s.strip()),
    )
