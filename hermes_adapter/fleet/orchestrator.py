"""Docker compose orchestration — generate per-agent overrides, run
``docker compose up -d --force-recreate --no-deps``, and wait for
health.

All input is validated against strict regexes before it reaches the
filesystem or the docker CLI. No shell=True, no string interpolation
into commands — we exec a fixed list of argv strings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

FLEET_ROOT = Path(os.environ.get("FLEET_ROOT", "/srv/hermes-fleet"))
COMPOSE_FILE = FLEET_ROOT / "docker-compose.yml"
OVERRIDE_DIR = FLEET_ROOT / "compose.override"
WORKSPACES_DIR = FLEET_ROOT / "workspaces"
AGENTS_DIR = FLEET_ROOT / "agents"

# Timeout budgets (seconds).
COMPOSE_UP_TIMEOUT = 60
HEALTH_WAIT_TIMEOUT = 30
HEALTH_POLL_INTERVAL = 1.0

# Input validation. Agent names match the `hermes-agent-<name>` service
# naming install-fleet.sh generates. User names match GitHub logins
# (which is what Studio passes today) plus a little room for future
# shapes. Anything else is rejected with 400.
AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FleetError(Exception):
    """Base class — has a structured (status, reason, message) triple."""

    status: int = 500
    reason: str = "internal_error"

    def __init__(self, message: str, *, status: Optional[int] = None, reason: Optional[str] = None):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status
        if reason is not None:
            self.reason = reason


class ValidationError(FleetError):
    status = 400
    reason = "invalid_input"


class UnknownAgentError(FleetError):
    status = 404
    reason = "unknown_agent"


class ComposeError(FleetError):
    status = 500
    reason = "compose_failed"


class HealthTimeoutError(FleetError):
    status = 504
    reason = "health_timeout"


# ---------------------------------------------------------------------------
# Status record
# ---------------------------------------------------------------------------


@dataclass
class AgentStatus:
    name: str
    current_user: Optional[str]
    healthy: bool
    container_id: Optional[str]
    last_claimed_at: Optional[float]  # unix seconds


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_agent(agent: str) -> str:
    if not isinstance(agent, str) or not AGENT_RE.match(agent):
        raise ValidationError(
            "agent must be lowercase alphanumeric + hyphen/underscore (max 64 chars)"
        )
    return agent


def _validate_user(user: str) -> str:
    if not isinstance(user, str) or not USER_RE.match(user):
        raise ValidationError(
            "user must be alphanumeric + dot/dash/underscore (max 128 chars)"
        )
    return user


def _assert_agent_exists(agent: str) -> None:
    """Refuse to operate on an agent that wasn't bootstrapped by install-fleet.sh."""
    agent_dir = AGENTS_DIR / agent
    if not agent_dir.is_dir():
        raise UnknownAgentError(
            f"agent {agent!r} has no persona dir at {agent_dir} — "
            "is it part of this fleet?"
        )


def _assert_fleet_ready() -> None:
    if not COMPOSE_FILE.is_file():
        raise ComposeError(
            f"no docker-compose.yml at {COMPOSE_FILE} — "
            "was install-fleet.sh run against $FLEET_ROOT?"
        )
    if not shutil.which("docker"):
        raise ComposeError(
            "docker CLI not found — adapter image must include docker "
            "and the host socket must be mounted"
        )


# ---------------------------------------------------------------------------
# Compose override generation
# ---------------------------------------------------------------------------


def _override_path(agent: str) -> Path:
    """Deterministic path for an agent's override file. Agent name is
    already validated, so no path-injection risk here."""
    return OVERRIDE_DIR / f"{agent}.yml"


def _render_claimed_override(agent: str, user: str) -> str:
    """YAML text pinning the agent's workspace volume to a specific user.

    We write the full volume list (not just the workspace one) so the
    override is self-contained — compose's volume merging semantics
    differ across versions, and ambiguity here is a security bug. The
    agent's persona/data mount and persona.md are copied from the base
    service definition.
    """
    # These must match the volumes install-fleet.sh writes for each
    # agent service. If install-fleet.sh changes the base mounts,
    # update here too.
    return (
        f"# Generated by hermes_adapter.fleet — DO NOT EDIT BY HAND.\n"
        f"# Regenerated on every /fleet/claim for agent {agent}.\n"
        f"services:\n"
        f"  hermes-agent-{agent}:\n"
        f"    volumes:\n"
        f"      - ./workspaces/{user}:/opt/workspaces\n"
        f"      - ./agents/{agent}:/opt/data\n"
        f"      - ./agents/{agent}/persona.md:/opt/hermes/docker/personas/{agent}.md\n"
        f"    labels:\n"
        f"      - hermes.fleet.current_user={user}\n"
        f"      - hermes.fleet.claimed_at={int(time.time())}\n"
    )


def _render_unclaimed_override(agent: str) -> str:
    """YAML for the post-unclaim state: placeholder mount, no user.

    We cannot simply delete the override and fall back to the base
    because the base service definition (from install-fleet.sh) has
    its own `./workspaces:/opt/workspaces` line which would re-expose
    the shared tree. The unclaimed override explicitly pins the
    workspace mount to an empty sentinel directory.
    """
    return (
        f"# Generated by hermes_adapter.fleet — DO NOT EDIT BY HAND.\n"
        f"# Unclaimed state for agent {agent}.\n"
        f"services:\n"
        f"  hermes-agent-{agent}:\n"
        f"    volumes:\n"
        f"      - ./workspaces/_unclaimed:/opt/workspaces:ro\n"
        f"      - ./agents/{agent}:/opt/data\n"
        f"      - ./agents/{agent}/persona.md:/opt/hermes/docker/personas/{agent}.md\n"
        f"    labels:\n"
        f"      - hermes.fleet.current_user=\n"
        f"      - hermes.fleet.claimed_at=\n"
    )


def _ensure_sentinel() -> None:
    """Ensure $FLEET_ROOT/workspaces/_unclaimed exists and is empty — the
    placeholder mount for unclaimed agents."""
    sentinel = WORKSPACES_DIR / "_unclaimed"
    sentinel.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Docker compose invocation
# ---------------------------------------------------------------------------


async def _docker_compose(*args: str, timeout: int = COMPOSE_UP_TIMEOUT) -> subprocess.CompletedProcess:
    """Run ``docker compose`` against the fleet's compose file + any
    currently-written override files. Returns CompletedProcess; raises
    ComposeError on non-zero exit or timeout."""
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE)]
    # Include all override files — agents that are unclaimed keep their
    # sentinel override, agents that are claimed have their user-specific
    # override. Both are merged with the base compose.
    if OVERRIDE_DIR.is_dir():
        for o in sorted(OVERRIDE_DIR.glob("*.yml")):
            cmd.extend(["-f", str(o)])
    cmd.extend(args)

    logger.debug("docker compose: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=FLEET_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ComposeError(
            f"docker compose timed out after {timeout}s: {' '.join(args)}"
        )

    result = subprocess.CompletedProcess(
        args=cmd, returncode=proc.returncode or 0, stdout=stdout, stderr=stderr
    )
    if result.returncode != 0:
        tail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-5:]
        raise ComposeError(
            f"docker compose exited {result.returncode}: {' '.join(args)}\n"
            f"stderr tail:\n  " + "\n  ".join(tail)
        )
    return result


async def _docker_inspect_label(
    container_name: str, label: str
) -> Optional[str]:
    """Read a container label via ``docker inspect``. Returns None if
    the container doesn't exist or the label isn't set."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        "--format",
        "{{ index .Config.Labels " + json.dumps(label) + " }}",
        container_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    value = stdout.decode("utf-8", "replace").strip()
    return value or None


async def _docker_inspect_health(container_name: str) -> tuple[bool, Optional[str]]:
    """Return (running, container_id) for a container. `running` is True
    when the container's State.Running is true."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        "--format",
        "{{.Id}}|{{.State.Running}}",
        container_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return (False, None)
    line = stdout.decode("utf-8", "replace").strip()
    if "|" not in line:
        return (False, None)
    cid, running = line.split("|", 1)
    return (running.lower() == "true", cid[:12] or None)


# ---------------------------------------------------------------------------
# Health wait — HTTP probe against the restarted container
# ---------------------------------------------------------------------------


async def _wait_healthy(agent: str, timeout: int = HEALTH_WAIT_TIMEOUT) -> None:
    """Poll the agent's in-network OpenAI-compat `/v1/health` endpoint
    until it returns 200 or timeout expires. Runs over the compose
    network via the service DNS name — no Caddy involvement."""
    # install-fleet.sh sets the OpenAI API port to 8642 inside the
    # network. The service hostname is `hermes-agent-<agent>`.
    import aiohttp  # local import — aiohttp is already in our deps

    url = f"http://hermes-agent-{agent}:8642/v1/health"
    deadline = time.monotonic() + timeout
    last_err: Optional[str] = None
    async with aiohttp.ClientSession() as sess:
        while time.monotonic() < deadline:
            try:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=3)) as r:
                    if r.status == 200:
                        return
                    last_err = f"status {r.status}"
            except Exception as e:  # noqa: BLE001 — connection refused, etc.
                last_err = str(e)
            await asyncio.sleep(HEALTH_POLL_INTERVAL)
    raise HealthTimeoutError(
        f"agent {agent} did not become healthy within {timeout}s (last: {last_err})"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def claim_agent(agent: str, user: str) -> dict:
    """Bind-mount *user*'s workspace into the *agent* container and
    force-recreate it. Returns a status dict on success; raises
    FleetError subclass on failure."""
    _assert_fleet_ready()
    agent = _validate_agent(agent)
    user = _validate_user(user)
    _assert_agent_exists(agent)

    # Ensure the user's workspace dir exists (first-claim case).
    user_dir = WORKSPACES_DIR / user
    user_dir.mkdir(parents=True, exist_ok=True)

    OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_sentinel()

    # Write the claimed override.
    _override_path(agent).write_text(_render_claimed_override(agent, user))

    # Force-recreate just this agent.
    container = f"hermes-agent-{agent}"
    await _docker_compose(
        "up", "-d", "--force-recreate", "--no-deps", container,
        timeout=COMPOSE_UP_TIMEOUT,
    )
    await _wait_healthy(agent)

    running, cid = await _docker_inspect_health(container)
    return {
        "ok": True,
        "agent": agent,
        "user": user,
        "container_id": cid,
        "healthy": running,
    }


async def unclaim_agent(agent: str) -> dict:
    """Tear down the user-specific mount on *agent* and recreate with
    the sentinel placeholder so the container sees nothing."""
    _assert_fleet_ready()
    agent = _validate_agent(agent)
    _assert_agent_exists(agent)

    OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_sentinel()

    # Swap the override to the sentinel version.
    _override_path(agent).write_text(_render_unclaimed_override(agent))

    container = f"hermes-agent-{agent}"
    await _docker_compose(
        "up", "-d", "--force-recreate", "--no-deps", container,
        timeout=COMPOSE_UP_TIMEOUT,
    )
    # Don't require health here — an unclaimed agent is still a valid
    # state even if the health endpoint lags.
    running, cid = await _docker_inspect_health(container)
    return {"ok": True, "agent": agent, "container_id": cid, "running": running}


async def get_status(agent: Optional[str] = None) -> list[AgentStatus]:
    """Return status for all fleet agents, or just *agent* if given.

    Current-user comes from the container's `hermes.fleet.current_user`
    label, which _render_claimed_override writes. Healthy is the
    container's State.Running flag (not /v1/health — that's a separate
    concern checked at claim time)."""
    _assert_fleet_ready()
    if agent is not None:
        agent = _validate_agent(agent)
        agents = [agent]
    else:
        # Enumerate from $FLEET_ROOT/agents/<name>/ directories.
        if not AGENTS_DIR.is_dir():
            return []
        agents = sorted(
            p.name for p in AGENTS_DIR.iterdir()
            if p.is_dir() and AGENT_RE.match(p.name)
        )

    out: list[AgentStatus] = []
    for a in agents:
        container = f"hermes-agent-{a}"
        current_user = await _docker_inspect_label(container, "hermes.fleet.current_user")
        claimed_at_str = await _docker_inspect_label(container, "hermes.fleet.claimed_at")
        running, cid = await _docker_inspect_health(container)
        claimed_at: Optional[float] = None
        if claimed_at_str:
            try:
                claimed_at = float(claimed_at_str)
            except ValueError:
                claimed_at = None
        out.append(
            AgentStatus(
                name=a,
                current_user=current_user or None,
                healthy=running,
                container_id=cid,
                last_claimed_at=claimed_at,
            )
        )
    return out
