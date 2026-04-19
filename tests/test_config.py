from __future__ import annotations

import pytest

from hermes_adapter.config import load


def test_defaults(monkeypatch: pytest.MonkeyPatch):
    for key in (
        "HERMES_WORKSPACE_DIR", "HERMES_ADAPTER_HOST", "HERMES_ADAPTER_PORT",
        "A2A_HOST", "A2A_PORT", "A2A_KEY", "A2A_PUBLIC_URL",
        "AGENT_NAME", "AGENT_DESCRIPTION", "AGENT_SKILLS", "AGENT_MODEL",
        "A2A_TOOLSETS",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = load()
    assert cfg.workspace_root == "/workspaces"
    assert cfg.workspace_port == 8766
    assert cfg.a2a_port == 9000
    assert cfg.a2a_key is None
    assert cfg.agent_name == "hermes-agent"
    assert cfg.agent_skills == ()
    assert cfg.a2a_toolsets == ()


def test_env_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_ADAPTER_PORT", "18766")
    monkeypatch.setenv("A2A_PORT", "19000")
    monkeypatch.setenv("AGENT_SKILLS", "code, research, ops")
    monkeypatch.setenv("A2A_TOOLSETS", "a, b")
    cfg = load()
    assert cfg.workspace_port == 18766
    assert cfg.a2a_port == 19000
    assert cfg.agent_skills == ("code", "research", "ops")
    assert cfg.a2a_toolsets == ("a", "b")
