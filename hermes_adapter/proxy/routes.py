"""Streaming HTTP proxy: ``/proxy/{agent}/{provider}/{path:.*}``.

Forwards the request body to the upstream LLM provider with the
agent's real API key injected as ``Authorization: Bearer ...``. Streams
the response back chunk-by-chunk so SSE (``stream: true``) works
without buffering.

Security model:
  * Inbound ``Authorization`` header from the agent is **discarded**.
    The agent has no way to inject auth into the upstream call — only
    the adapter can.
  * No ``Host``, ``X-Forwarded-*``, or ``Cookie`` headers are forwarded
    upstream — only ``Content-Type`` and ``Accept``.
  * Response headers ``Content-Encoding``, ``Transfer-Encoding``, and
    ``Content-Length`` are stripped — aiohttp re-frames the response
    with its own chunked encoding and the upstream's encoding may not
    survive proxying intact.
"""

from __future__ import annotations

import logging

import aiohttp
from aiohttp import web

from .providers import PROVIDERS
from .secrets import read_agent_env

logger = logging.getLogger(__name__)

# Total deadline for one upstream call. LLM streams can run long;
# 10 minutes is more than any single chat turn but caps runaway
# connections. sock_read covers idle gaps inside a stream.
_UPSTREAM_TIMEOUT = aiohttp.ClientTimeout(total=600, sock_read=600)

# Headers we DO forward to the upstream provider. Everything else is
# dropped — Authorization in particular, since we substitute our own.
_INBOUND_FORWARD = {"content-type", "accept"}

# Response headers we strip before sending back to the agent. aiohttp
# re-encodes the body, so any framing the upstream set is invalid for
# the new transport.
_RESPONSE_STRIP = {"content-encoding", "transfer-encoding", "content-length", "connection"}


def _err(status: int, reason: str, message: str) -> web.Response:
    """Compact JSON error response — same shape as fleet/* handlers."""
    return web.json_response(
        {"ok": False, "reason": reason, "message": message},
        status=status,
    )


async def handle_proxy(request: web.Request) -> web.StreamResponse:
    """Proxy one HTTP request to the upstream LLM provider.

    URL contract: ``/proxy/{agent}/{provider}/{path:.*}``
      * ``{agent}``    — must be a known agent (has ``agents/<name>/.env``)
      * ``{provider}`` — must be in ``PROVIDERS``
      * ``{path}``     — appended verbatim to the provider's base URL
    """
    agent = request.match_info["agent"]
    provider_name = request.match_info["provider"]
    rest = request.match_info["path"]

    # 1. Validate provider — reject unknowns before reading anything else.
    provider = PROVIDERS.get(provider_name)
    if provider is None:
        return _err(404, "unknown_provider", f"no such provider: {provider_name!r}")

    # 2. Read agent's .env (raises ValueError on bad agent name).
    try:
        env = read_agent_env(agent)
    except ValueError as e:
        return _err(400, "bad_agent_name", str(e))

    api_key = env.get(provider.key_env)
    if not api_key:
        return _err(
            502,
            "key_not_configured",
            f"{provider.key_env} not set in agents/{agent}/.env — "
            f"run `./fleet set {agent} --key <key>` on the host",
        )

    # 3. Compute upstream URL. Agent's .env may override default base URL
    #    (e.g. minimax-cn region). rest is already URL-decoded by aiohttp.
    base_url = env.get(provider.base_url_env, provider.default_base_url).rstrip("/")
    upstream_url = f"{base_url}/{rest}"
    if request.query_string:
        upstream_url = f"{upstream_url}?{request.query_string}"

    # 4. Build the upstream headers from scratch — safe-list, not deny-list.
    upstream_headers = {"Authorization": f"Bearer {api_key}"}
    for k, v in request.headers.items():
        if k.lower() in _INBOUND_FORWARD:
            upstream_headers[k] = v

    body = await request.read()

    logger.info(
        "[proxy] %s agent=%s provider=%s upstream=%s bytes_in=%d",
        request.method, agent, provider_name, upstream_url, len(body),
    )

    # 5. Streaming pass-through. Open the upstream and immediately start
    #    writing chunks back to the client as they arrive — no buffering,
    #    so SSE deltas reach the agent in real time.
    session = aiohttp.ClientSession(timeout=_UPSTREAM_TIMEOUT)
    try:
        upstream = await session.request(
            request.method,
            upstream_url,
            headers=upstream_headers,
            data=body,
        )
    except aiohttp.ClientError as e:
        await session.close()
        return _err(502, "upstream_unreachable", f"{type(e).__name__}: {e}")

    response_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _RESPONSE_STRIP
    }
    response = web.StreamResponse(status=upstream.status, headers=response_headers)
    await response.prepare(request)
    try:
        async for chunk in upstream.content.iter_any():
            await response.write(chunk)
        await response.write_eof()
    finally:
        upstream.release()
        await session.close()
    return response
