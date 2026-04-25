"""Read agent .env files from the host (mounted into the adapter).

The adapter container has ``$FLEET_ROOT/agents/<name>/.env`` mounted
read-only via ``${FLEET_HOST_ROOT}:/srv/hermes-fleet`` (see
install-fleet.sh). This module is the only path that file gets read.

Caching: we re-read on every request. The file is on a local mount,
the read is microseconds, and re-reading lets ``./fleet set <name>
--key <new>`` take effect without restarting the adapter. If profiling
ever shows this as a hot path, add a TTL cache.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Same constraint as the fleet orchestrator and Traefik path regex —
# keep the agent name to a safe filesystem-friendly subset.
AGENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def fleet_root() -> Path:
    """Return the on-disk root the adapter has mounted.

    Default ``/srv/hermes-fleet`` matches install-fleet.sh; the env var
    override exists for tests."""
    return Path(os.environ.get("FLEET_ROOT", "/srv/hermes-fleet"))


def read_agent_env(agent: str) -> dict[str, str]:
    """Parse ``agent-secrets/<agent>/.env`` into a dict.

    The secret file lives under ``agent-secrets/`` (not ``agents/``) so
    the agent's own HERMES_HOME mount can NOT see it — that's the
    whole point of the proxy. The adapter sees both dirs because its
    ``${FLEET_HOST_ROOT}:/srv/hermes-fleet`` bind covers the parent.

    Returns an empty dict if the agent has no .env yet (e.g. before
    the operator has run ``./fleet set``). The proxy treats missing
    keys as 502 — never falls through to a no-auth upstream call.

    Raises ``ValueError`` if ``agent`` doesn't match ``AGENT_NAME_RE``
    — lets callers translate that to a 400 without doing the regex
    themselves.
    """
    if not AGENT_NAME_RE.match(agent):
        raise ValueError(f"invalid agent name: {agent!r}")
    env_path = fleet_root() / "agent-secrets" / agent / ".env"
    if not env_path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Strip optional surrounding quotes — matches docker compose's
        # env_file parser. Don't expand $VAR refs; agents shouldn't be
        # composing keys.
        value = value.strip().strip('"').strip("'")
        out[key.strip()] = value
    return out
