"""Offline tests for the A2A client helpers.

These use ``respx`` / mocking-via-httpx-transport only when feasible. To stay
light-weight we avoid an extra dep and instead test pure logic on the
schemas + the error branches that don't need a server.
"""

from __future__ import annotations

import json

from hermes_adapter.a2a import (
    A2A_CALL_SCHEMA,
    A2A_DISCOVER_SCHEMA,
    A2A_LOCAL_SCAN_SCHEMA,
    a2a_call,
    a2a_discover,
)


def test_schemas_have_required_shape():
    for schema in (A2A_DISCOVER_SCHEMA, A2A_CALL_SCHEMA, A2A_LOCAL_SCAN_SCHEMA):
        assert schema["name"]
        assert schema["description"]
        params = schema["parameters"]
        assert params["type"] == "object"
        assert "properties" in params


def test_discover_unreachable_returns_error_json():
    out = a2a_discover("http://127.0.0.1:1")  # port 1 — guaranteed closed
    assert "error" in json.loads(out)


def test_call_rejects_empty_message(monkeypatch):
    """The tool wrapper refuses empty messages without ever hitting the network."""
    from hermes_adapter.a2a.client import _tool_a2a_call

    # No message — should error before httpx is reached
    out = _tool_a2a_call({"url": "http://example.com"})
    assert "message is required" in json.loads(out)["error"]


def test_call_requires_url_or_agent_name():
    from hermes_adapter.a2a.client import _tool_a2a_call

    out = _tool_a2a_call({"message": "hi"})
    err = json.loads(out)["error"]
    assert "url" in err or "agent_name" in err
