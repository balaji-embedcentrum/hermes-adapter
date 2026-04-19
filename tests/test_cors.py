from __future__ import annotations

import pytest


@pytest.fixture
async def allowlist_client(aiohttp_client, workspace_root, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_ADAPTER_CORS_ORIGINS", "https://studio.example.com")
    from hermes_adapter.workspace.app import build_app

    return await aiohttp_client(build_app())


async def test_default_wildcard_allows_any_origin(client):
    resp = await client.get("/health", headers={"Origin": "https://foo.invalid"})
    assert resp.status == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "https://foo.invalid"


async def test_preflight_returns_cors_headers(client):
    resp = await client.options(
        "/ws/demo/file",
        headers={
            "Origin": "https://studio.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )
    assert resp.status == 204
    assert resp.headers["Access-Control-Allow-Origin"] == "https://studio.example.com"
    assert "POST" in resp.headers["Access-Control-Allow-Methods"]
    assert "authorization" in resp.headers["Access-Control-Allow-Headers"].lower()


async def test_allowlist_rejects_unlisted_origin(allowlist_client):
    resp = await allowlist_client.get(
        "/health", headers={"Origin": "https://evil.example.com"}
    )
    assert resp.status == 200
    assert resp.headers.get("Access-Control-Allow-Origin") is None


async def test_allowlist_allows_listed_origin(allowlist_client):
    resp = await allowlist_client.get(
        "/health", headers={"Origin": "https://studio.example.com"}
    )
    assert resp.status == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "https://studio.example.com"


async def test_no_origin_header_unchanged(client):
    """Non-CORS clients (curl, server-to-server) must still work."""
    resp = await client.get("/health")
    assert resp.status == 200
    assert "Access-Control-Allow-Origin" not in resp.headers
