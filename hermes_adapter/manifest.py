"""Declarative multi-agent manifest — ``agents.yaml``.

A single file describes the adapter + every hermes-a2a agent the user wants
running. ``hermes-adapter up`` reads this file, starts the workspace API,
and spawns one ``hermes-a2a`` subprocess per agent with that agent's own
``HERMES_HOME`` directory.

The default path is ``$HERMES_ADAPTER_HOME/agents.yaml`` where
``HERMES_ADAPTER_HOME`` defaults to ``~/.hermes-adapter``.

Schema:

    version: 1
    adapter:
      workspace_dir: ~/hermes-workspaces
      host: 127.0.0.1
      port: 8766
      cors_origins:
        - https://hermes-studio.com
    a2a_key: <bearer shared across agents, optional>
    agents:
      - name: alpha
        port: 9001
        model: anthropic/claude-sonnet-4.6
        description: "Code review"
        hermes_home: ~/.hermes-adapter/agents/alpha   # auto-filled by `agent add`
"""

from __future__ import annotations

import os
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_HOME = Path(os.environ.get("HERMES_ADAPTER_HOME", str(Path.home() / ".hermes-adapter")))
DEFAULT_MANIFEST = DEFAULT_HOME / "agents.yaml"
DEFAULT_WORKSPACE = Path(os.environ.get("HERMES_WORKSPACE_DIR", str(Path.home() / "hermes-workspaces")))
DEFAULT_AGENTS_DIR = DEFAULT_HOME / "agents"
DEFAULT_RUN_DIR = DEFAULT_HOME / "run"
DEFAULT_LOG_DIR = DEFAULT_HOME / "logs"


@dataclass
class AdapterBlock:
    workspace_dir: str = str(DEFAULT_WORKSPACE)
    host: str = "127.0.0.1"
    port: int = 8766
    # Known browser consumers of a user's local gateway. Extend with
    # `hermes-adapter init --cors-origins "..."` or by editing agents.yaml.
    # Using explicit origins rather than "*" so Studio / Akela can pass
    # credentialed requests later without breaking.
    cors_origins: list[str] = field(
        default_factory=lambda: ["https://hermes-studio.com", "https://akela-ai.com"]
    )


@dataclass
class AgentSpec:
    name: str
    port: int
    model: str
    description: str = ""
    hermes_home: str = ""   # filled in by `agent add`

    def resolved_home(self) -> Path:
        return Path(os.path.expanduser(self.hermes_home)) if self.hermes_home else (
            DEFAULT_AGENTS_DIR / self.name
        )


@dataclass
class Manifest:
    version: int = 1
    adapter: AdapterBlock = field(default_factory=AdapterBlock)
    a2a_key: str = ""
    agents: list[AgentSpec] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Manifest":
        path = path or DEFAULT_MANIFEST
        if not path.exists():
            raise FileNotFoundError(
                f"No manifest at {path}. Run `hermes-adapter init` first."
            )
        data = yaml.safe_load(path.read_text()) or {}
        adapter_data = data.get("adapter") or {}
        agents_data = data.get("agents") or []
        return cls(
            version=int(data.get("version", 1)),
            adapter=AdapterBlock(
                workspace_dir=adapter_data.get("workspace_dir", str(DEFAULT_WORKSPACE)),
                host=adapter_data.get("host", "127.0.0.1"),
                port=int(adapter_data.get("port", 8766)),
                cors_origins=list(adapter_data.get("cors_origins") or ["https://hermes-studio.com"]),
            ),
            a2a_key=str(data.get("a2a_key", "")),
            agents=[
                AgentSpec(
                    name=a["name"],
                    port=int(a["port"]),
                    model=str(a.get("model", "")),
                    description=str(a.get("description", "")),
                    hermes_home=str(a.get("hermes_home", "")),
                )
                for a in agents_data
            ],
        )

    def save(self, path: Path | None = None) -> None:
        path = path or DEFAULT_MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "version": self.version,
            "adapter": asdict(self.adapter),
            "a2a_key": self.a2a_key,
            "agents": [asdict(a) for a in self.agents],
        }
        path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
        os.chmod(path, 0o600)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def find(self, name: str) -> AgentSpec | None:
        for a in self.agents:
            if a.name == name:
                return a
        return None

    def add(self, spec: AgentSpec) -> None:
        if self.find(spec.name):
            raise ValueError(f"Agent '{spec.name}' already exists")
        if any(a.port == spec.port for a in self.agents):
            raise ValueError(f"Port {spec.port} is already in use by another agent")
        if spec.port == self.adapter.port:
            raise ValueError(f"Port {spec.port} collides with the adapter port")
        self.agents.append(spec)

    def remove(self, name: str) -> AgentSpec:
        spec = self.find(name)
        if not spec:
            raise KeyError(f"No agent named '{name}'")
        self.agents.remove(spec)
        return spec

    def next_free_port(self, start: int = 9001) -> int:
        used = {a.port for a in self.agents} | {self.adapter.port}
        port = start
        while port in used:
            port += 1
        return port


def default_manifest() -> Manifest:
    """Fresh manifest with a generated bearer token and no agents yet."""
    return Manifest(
        version=1,
        adapter=AdapterBlock(),
        a2a_key=secrets.token_urlsafe(32),
        agents=[],
    )


# ---------------------------------------------------------------------------
# Per-agent HERMES_HOME scaffolding
# ---------------------------------------------------------------------------

_PROVIDER_ENV = {
    # model prefix → env var name hermes-agent expects
    "anthropic/": "ANTHROPIC_API_KEY",
    "openai/": "OPENAI_API_KEY",
    "google/": "GEMINI_API_KEY",
    "gemini/": "GEMINI_API_KEY",
    "openrouter/": "OPENROUTER_API_KEY",
    "mistral/": "MISTRAL_API_KEY",
    "minimax/": "MINIMAX_API_KEY",
    "minimax-cn/": "MINIMAX_API_KEY",
    "deepseek/": "DEEPSEEK_API_KEY",
    "nous/": "NOUS_API_KEY",
    "zai/": "ZAI_API_KEY",
    "glm/": "GLM_API_KEY",
    "kimi/": "KIMI_API_KEY",
    "moonshot/": "KIMI_API_KEY",
    "dashscope/": "DASHSCOPE_API_KEY",
    "qwen/": "DASHSCOPE_API_KEY",
    "xiaomi/": "XIAOMI_API_KEY",
    "kilocode/": "KILOCODE_API_KEY",
}


def provider_env_var(model: str) -> str:
    """Guess the env var this model needs. Defaults to OPENAI_API_KEY for
    OpenAI-compatible local endpoints (Ollama, vLLM, LM Studio, ...)."""
    for prefix, var in _PROVIDER_ENV.items():
        if model.startswith(prefix):
            return var
    return "OPENAI_API_KEY"


def write_agent_home(spec: AgentSpec, provider_key: str | None = None, base_url: str | None = None) -> Path:
    """Create (if missing) the per-agent HERMES_HOME folder with .env + config.yaml."""
    home = spec.resolved_home()
    home.mkdir(parents=True, exist_ok=True)

    env_file = home / ".env"
    if not env_file.exists():
        lines: list[str] = []
        var = provider_env_var(spec.model)
        if provider_key:
            lines.append(f"{var}={provider_key}")
        else:
            lines.append(f"# {var}=<set your key here>")
        if base_url:
            lines.append(f"OPENAI_BASE_URL={base_url}")
        env_file.write_text("\n".join(lines) + "\n")
        os.chmod(env_file, 0o600)

    cfg_file = home / "config.yaml"
    if not cfg_file.exists():
        cfg = {"model": {"default": spec.model}}
        cfg_file.write_text(yaml.safe_dump(cfg, sort_keys=False))

    return home
