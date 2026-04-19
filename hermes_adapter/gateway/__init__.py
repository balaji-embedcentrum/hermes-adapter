"""Unified per-agent gateway — one HTTP server serving every contract.

Every running hermes-a2a-style child process (spawned by the supervisor) is
replaced by one of these. A single port per agent serves:

    /health                     plain OK (Studio connect ping)
    /v1/models                  OpenAI-compatible model discovery (Studio)
    /v1/chat/completions        OpenAI-compatible SSE chat (Studio + Akela fallback)
    /.well-known/agent.json     A2A Agent Card v0.4.x (Akela primary)
    /.well-known/agent-card.json A2A Agent Card v0.3.x fallback (Akela)
    POST /                      A2A JSON-RPC message/send (Akela A2A mode)
    /ws                         workspace listing (Studio)
    /ws/{repo}/tree|file|...    workspace filesystem + git (Studio)

CORS is wide-open so a browser loaded from any origin (Akela's hosted UI,
Hermes Studio, etc.) can call the gateway directly.
"""

from .app import build_app, run

__all__ = ["build_app", "run"]
