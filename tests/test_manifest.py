from __future__ import annotations

from pathlib import Path

import pytest

from hermes_adapter.manifest import (
    AgentSpec,
    Manifest,
    default_manifest,
    provider_env_var,
    write_agent_home,
)


def test_provider_env_var_prefixes():
    assert provider_env_var("anthropic/claude-sonnet-4.6") == "ANTHROPIC_API_KEY"
    assert provider_env_var("openai/gpt-5") == "OPENAI_API_KEY"
    assert provider_env_var("google/gemini-2.0-flash") == "GEMINI_API_KEY"
    assert provider_env_var("openrouter/meta-llama/llama-3.1-70b") == "OPENROUTER_API_KEY"
    # unknown → fall back to OpenAI-compatible
    assert provider_env_var("mystery/model") == "OPENAI_API_KEY"


def test_default_manifest_has_fresh_bearer():
    m = default_manifest()
    assert m.version == 1
    assert m.agents == []
    assert len(m.a2a_key) >= 32  # secrets.token_urlsafe(32) → 43+ chars base64


def test_add_rejects_duplicates_and_port_collisions():
    m = default_manifest()
    m.add(AgentSpec(name="alpha", port=9001, model="anthropic/claude-sonnet-4.6"))
    with pytest.raises(ValueError, match="already exists"):
        m.add(AgentSpec(name="alpha", port=9002, model="openai/gpt-5"))
    with pytest.raises(ValueError, match="already in use"):
        m.add(AgentSpec(name="beta", port=9001, model="openai/gpt-5"))


def test_add_rejects_adapter_port_collision():
    m = default_manifest()
    with pytest.raises(ValueError, match="adapter port"):
        m.add(AgentSpec(name="alpha", port=m.adapter.port, model="openai/gpt-5"))


def test_next_free_port_skips_used():
    m = default_manifest()
    m.add(AgentSpec(name="alpha", port=9001, model="x"))
    m.add(AgentSpec(name="beta", port=9003, model="x"))
    assert m.next_free_port() == 9002


def test_save_and_load_roundtrip(tmp_path: Path):
    m = default_manifest()
    m.adapter.workspace_dir = "/tmp/ws"
    m.adapter.cors_origins = ["https://a.example", "https://b.example"]
    m.add(AgentSpec(name="alpha", port=9001, model="anthropic/claude-sonnet-4.6", description="code"))
    m.add(AgentSpec(name="beta", port=9002, model="openai/gpt-5"))

    path = tmp_path / "agents.yaml"
    m.save(path)
    assert path.stat().st_mode & 0o777 == 0o600  # chmod 600

    m2 = Manifest.load(path)
    assert m2.adapter.workspace_dir == "/tmp/ws"
    assert m2.adapter.cors_origins == ["https://a.example", "https://b.example"]
    assert m2.a2a_key == m.a2a_key
    assert [a.name for a in m2.agents] == ["alpha", "beta"]
    assert m2.find("alpha").model == "anthropic/claude-sonnet-4.6"


def test_load_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="hermes-adapter init"):
        Manifest.load(tmp_path / "nope.yaml")


def test_write_agent_home_scaffolds_env_and_config(tmp_path: Path):
    spec = AgentSpec(
        name="alpha",
        port=9001,
        model="anthropic/claude-sonnet-4.6",
        hermes_home=str(tmp_path / "alpha"),
    )
    home = write_agent_home(spec, provider_key="sk-ant-test")

    assert home == tmp_path / "alpha"
    assert (home / ".env").read_text().strip() == "ANTHROPIC_API_KEY=sk-ant-test"
    assert (home / ".env").stat().st_mode & 0o777 == 0o600
    assert "claude-sonnet-4.6" in (home / "config.yaml").read_text()


def test_write_agent_home_handles_missing_key(tmp_path: Path):
    spec = AgentSpec(
        name="beta",
        port=9002,
        model="openai/gpt-5",
        hermes_home=str(tmp_path / "beta"),
    )
    home = write_agent_home(spec, provider_key=None)
    env = (home / ".env").read_text()
    assert "# OPENAI_API_KEY=<set your key here>" in env


def test_write_agent_home_with_base_url(tmp_path: Path):
    spec = AgentSpec(
        name="gamma",
        port=9003,
        model="openai/llama3.1",
        hermes_home=str(tmp_path / "gamma"),
    )
    home = write_agent_home(spec, provider_key="dummy", base_url="http://localhost:11434/v1")
    env = (home / ".env").read_text()
    assert "OPENAI_API_KEY=dummy" in env
    assert "OPENAI_BASE_URL=http://localhost:11434/v1" in env
