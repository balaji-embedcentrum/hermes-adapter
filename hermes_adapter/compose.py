"""Emit a ``docker-compose.yml`` from the adapter's ``agents.yaml`` manifest.

The generated compose file is self-contained: one ``adapter`` service running
the workspace API plus one ``hermes-agent-<name>`` service per configured
agent running ``hermes-a2a``. The same ``agents.yaml`` that drives the local
Python supervisor drives the Docker layout, so a user can switch runtimes
without re-typing their agent configs.

Volumes:
  - ``HERMES_WORKSPACE_DIR`` is mounted at /workspaces in every agent + the adapter
  - Each agent's ``HERMES_HOME`` directory is mounted at /root/.hermes

Networking:
  - ``adapter`` publishes the workspace port on 127.0.0.1 by default
  - Each agent publishes its A2A port on 127.0.0.1 by default

To expose them on the public internet (VPS use), front the stack with a
reverse proxy that terminates TLS and route ``/ws/*`` + ``/a2a/<name>/*``.
"""

from __future__ import annotations

from typing import Any

import yaml

from .manifest import Manifest


def build_compose(
    manifest: Manifest,
    image: str = "ghcr.io/balaji-embedcentrum/hermes-adapter:latest",
    hermes_agent_image: str = "noushermes/hermes-agent:latest",
    bind_address: str = "127.0.0.1",
) -> dict[str, Any]:
    """Return a Python dict representing the full docker-compose document."""
    services: dict[str, Any] = {}

    services["adapter"] = {
        "image": image,
        "container_name": "hermes-adapter",
        "restart": "unless-stopped",
        "command": ["hermes-adapter", "workspace"],
        "environment": {
            "HERMES_ADAPTER_HOST": "0.0.0.0",
            "HERMES_ADAPTER_PORT": str(manifest.adapter.port),
            "HERMES_WORKSPACE_DIR": "/workspaces",
            "HERMES_ADAPTER_CORS_ORIGINS": ",".join(manifest.adapter.cors_origins),
        },
        "volumes": [f"{manifest.adapter.workspace_dir}:/workspaces"],
        "ports": [f"{bind_address}:{manifest.adapter.port}:{manifest.adapter.port}"],
    }

    for spec in manifest.agents:
        env = {
            "A2A_HOST": "0.0.0.0",
            "A2A_PORT": str(spec.port),
            "AGENT_NAME": spec.name,
            "AGENT_DESCRIPTION": spec.description or spec.name,
        }
        if manifest.a2a_key:
            env["A2A_KEY"] = manifest.a2a_key

        services[f"hermes-agent-{spec.name}"] = {
            "image": hermes_agent_image,
            "container_name": f"hermes-agent-{spec.name}",
            "restart": "unless-stopped",
            "command": ["hermes-a2a"],
            "env_file": [f"{spec.resolved_home()}/.env"],
            "environment": env,
            "volumes": [
                f"{manifest.adapter.workspace_dir}:/workspaces",
                f"{spec.resolved_home()}:/root/.hermes",
            ],
            "ports": [f"{bind_address}:{spec.port}:{spec.port}"],
        }

    return {
        "name": "hermes-adapter-stack",
        "services": services,
    }


def dump_compose(manifest: Manifest, **kwargs) -> str:
    """Serialize the compose dict as YAML text."""
    doc = build_compose(manifest, **kwargs)
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
