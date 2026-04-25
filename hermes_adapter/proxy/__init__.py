"""LLM provider proxy — keeps provider API keys out of agent containers.

The fleet's per-agent containers are exposed to user-controlled prompts.
A prompt-injection attack against an agent should not be able to leak
``MINIMAX_API_KEY`` (or any other provider key). To enforce this:

  * Provider keys live ONLY in ``$FLEET_ROOT/agents/<name>/.env`` on the
    host. That file is mounted into the **adapter** container at
    ``/srv/hermes-fleet/agents/<name>/.env`` — never into the agent.
  * Agents are configured to point their LLM client at this proxy
    (``MINIMAX_API_BASE=http://adapter:8766/proxy/<agent>/minimax/v1``).
  * On every call, the adapter looks up the agent's real provider key
    from the on-disk ``.env``, injects the ``Authorization`` header, and
    streams the upstream response back. The agent never sees the key,
    never has it in ``os.environ``, never can read it from a file.

This is a security boundary — kernel-enforced filesystem isolation
combined with adapter-mediated egress. Prompt injection cannot defeat
either.

See ``hermes_adapter.proxy.routes.handle_proxy`` for the request flow.
"""
