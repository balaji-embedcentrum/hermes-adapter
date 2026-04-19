"""Gateway integration tests using Starlette's TestClient.

We don't exercise the full hermes-agent path (that requires a real
provider key). Instead we verify every endpoint the gateway exposes
returns the expected shape / status, and that CORS works for browser
clients.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from hermes_adapter.gateway.app import build_app
from hermes_adapter.workspace import symbols_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    symbols_cache._cache.clear()
    yield
    symbols_cache._cache.clear()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_NAME", "test-alpha")
    monkeypatch.setenv("AGENT_DESCRIPTION", "test agent")
    monkeypatch.setenv("A2A_PUBLIC_URL", "http://127.0.0.1:9001")
    app = build_app(port=9001)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "hermes-adapter-gateway"
    assert body["agent"] == "test-alpha"


def test_v1_health_alias(client):
    r = client.get("/v1/health")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /v1/models
# ---------------------------------------------------------------------------


def test_v1_models_returns_openai_shape(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    model = body["data"][0]
    assert model["object"] == "model"
    assert "id" in model
    assert model["owned_by"] == "test-alpha"


# ---------------------------------------------------------------------------
# /v1/chat/completions validation
# ---------------------------------------------------------------------------


def test_chat_completions_rejects_empty_body(client):
    r = client.post("/v1/chat/completions", content=b"")
    assert r.status_code == 400


def test_chat_completions_rejects_missing_messages(client):
    r = client.post("/v1/chat/completions", json={})
    assert r.status_code == 400
    assert "messages" in r.json()["error"]["message"]


def test_chat_completions_rejects_empty_messages(client):
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": ""}]})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# A2A Agent Card
# ---------------------------------------------------------------------------


def test_agent_card_v04(client):
    r = client.get("/.well-known/agent.json")
    assert r.status_code == 200
    card = r.json()
    assert card["name"] == "test-alpha"
    assert card["url"] == "http://127.0.0.1:9001"
    assert card["capabilities"]["streaming"] is True
    assert isinstance(card["skills"], list)


def test_agent_card_v03_fallback(client):
    r = client.get("/.well-known/agent-card.json")
    assert r.status_code == 200
    assert r.json()["name"] == "test-alpha"


def test_jsonrpc_rejects_unknown_method(client):
    r = client.post(
        "/",
        json={"jsonrpc": "2.0", "id": 1, "method": "unknown/method", "params": {}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"]["code"] == -32601
    assert body["id"] == 1


def test_jsonrpc_rejects_bad_json(client):
    r = client.post("/", content=b"not json")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32700


def test_jsonrpc_rejects_message_with_no_text(client):
    r = client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {"message": {"parts": []}},
        },
    )
    body = r.json()
    assert body["error"]["code"] == -32602


def test_jsonrpc_message_stream_accepted(client):
    """message/stream returns an SSE response, not JSON-RPC-only 404."""
    with client.stream(
        "POST",
        "/",
        json={
            "jsonrpc": "2.0",
            "id": "t1",
            "method": "message/stream",
            "params": {
                "message": {
                    "messageId": "m1",
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                }
            },
        },
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        # First frame should be a status: working event, even if hermes-agent
        # isn't installed (we'll hit the RuntimeError path after — but the
        # handler must at least produce the initial SSE event).
        first_line = None
        for raw in resp.iter_lines():
            if raw and raw.startswith("data:"):
                first_line = raw
                break
        assert first_line is not None
        import json

        payload = json.loads(first_line[len("data:"):].strip())
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == "t1"
        assert payload["result"]["taskId"]
        # First event is `status: working` per A2A v0.4.x
        assert payload["result"].get("status", {}).get("state") == "working"


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


def test_ws_list_empty(client):
    r = client.get("/ws")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["workspaces"] == []


def test_ws_tree_missing_repo(client):
    r = client.get("/ws/ghost/tree")
    assert r.status_code == 404


def test_ws_file_post_traversal_rejected(client, tmp_path: Path):
    # Create a repo so the endpoint gets past the find_repo guard.
    import subprocess
    (tmp_path / "alice" / "demo").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path / "alice" / "demo", check=True)

    r = client.post(
        "/ws/demo/file", json={"path": "../outside.txt", "content": "x"}
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


def test_cors_allows_any_origin_by_default(client):
    r = client.get("/health", headers={"Origin": "https://akela-ai.com"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") in ("*", "https://akela-ai.com")


def test_cors_preflight_for_chat(client):
    r = client.options(
        "/v1/chat/completions",
        headers={
            "Origin": "https://akela-ai.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") in ("*", "https://akela-ai.com")
    assert "POST" in r.headers.get("access-control-allow-methods", "").upper()


def test_cors_preflight_for_agent_card(client):
    # Akela's first probe is a CORS-preflighted GET for the Agent Card
    r = client.options(
        "/.well-known/agent.json",
        headers={
            "Origin": "https://akela-ai.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") in ("*", "https://akela-ai.com")


def test_cors_allowlist(client, monkeypatch):
    """When HERMES_ADAPTER_CORS_ORIGINS is set, only listed origins get the header."""
    monkeypatch.setenv(
        "HERMES_ADAPTER_CORS_ORIGINS", "https://hermes-studio.com,https://akela-ai.com"
    )
    app = build_app(port=9001)
    with TestClient(app) as c:
        r_allowed = c.get("/health", headers={"Origin": "https://akela-ai.com"})
        assert r_allowed.headers.get("access-control-allow-origin") == "https://akela-ai.com"

        r_blocked = c.get("/health", headers={"Origin": "https://evil.example"})
        # Starlette's CORSMiddleware drops the header entirely for non-listed origins
        assert r_blocked.headers.get("access-control-allow-origin") is None
