from __future__ import annotations

import yaml

from hermes_adapter.compose import build_compose, dump_compose
from hermes_adapter.manifest import AgentSpec, default_manifest


def _manifest_with_two_agents():
    m = default_manifest()
    m.adapter.workspace_dir = "/srv/ws"
    m.adapter.cors_origins = ["https://hermes-studio.com"]
    m.add(AgentSpec(name="alpha", port=9001, model="anthropic/claude-sonnet-4.6", description="Code review"))
    m.add(AgentSpec(name="beta", port=9002, model="openai/gpt-5"))
    return m


def test_compose_has_adapter_and_every_agent():
    doc = build_compose(_manifest_with_two_agents(), bind_address="0.0.0.0")
    assert doc["name"] == "hermes-adapter-stack"
    services = doc["services"]
    assert set(services.keys()) == {"adapter", "hermes-agent-alpha", "hermes-agent-beta"}


def test_adapter_service_has_no_model_env_but_has_cors():
    m = _manifest_with_two_agents()
    svc = build_compose(m)["services"]["adapter"]
    env = svc["environment"]
    assert "HERMES_ADAPTER_CORS_ORIGINS" in env
    assert env["HERMES_ADAPTER_CORS_ORIGINS"] == "https://hermes-studio.com"
    # adapter must never carry provider keys
    assert not any(k.endswith("_API_KEY") for k in env)


def test_agent_service_mounts_its_own_home_and_loads_env_file():
    m = _manifest_with_two_agents()
    alpha = build_compose(m)["services"]["hermes-agent-alpha"]
    home = m.find("alpha").resolved_home()
    assert f"{home}:/root/.hermes" in alpha["volumes"]
    assert alpha["env_file"] == [f"{home}/.env"]
    assert alpha["environment"]["AGENT_NAME"] == "alpha"
    assert alpha["environment"]["A2A_PORT"] == "9001"
    assert alpha["environment"]["A2A_KEY"] == m.a2a_key


def test_bind_address_controls_port_exposure():
    m = _manifest_with_two_agents()
    doc = build_compose(m, bind_address="127.0.0.1")
    assert doc["services"]["adapter"]["ports"] == ["127.0.0.1:8766:8766"]
    assert doc["services"]["hermes-agent-alpha"]["ports"] == ["127.0.0.1:9001:9001"]

    doc = build_compose(m, bind_address="0.0.0.0")
    assert doc["services"]["adapter"]["ports"] == ["0.0.0.0:8766:8766"]


def test_dump_compose_is_parseable_yaml():
    m = _manifest_with_two_agents()
    text = dump_compose(m)
    parsed = yaml.safe_load(text)
    assert parsed["name"] == "hermes-adapter-stack"
    assert "adapter" in parsed["services"]


def test_empty_agents_produces_adapter_only():
    m = default_manifest()
    m.adapter.workspace_dir = "/tmp/ws"
    doc = build_compose(m)
    assert list(doc["services"].keys()) == ["adapter"]
