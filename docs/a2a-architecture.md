# A2A server architecture

The A2A server exposes a local `hermes-agent` over the [Google Agent-to-Agent (A2A) protocol](https://github.com/a2aproject/A2A) so standard orchestrators can discover and call it.

## Wire format

A2A is HTTP + JSON-RPC 2.0:

| Method | Path | Purpose |
|-------|------|---------|
| `GET`  | `/.well-known/agent.json` | Agent Card — name, description, skills, streaming capability |
| `POST` | `/`                       | JSON-RPC: `tasks/send`, `tasks/sendSubscribe` |

`tasks/sendSubscribe` streams incremental `TaskArtifactUpdateEvent` and `TaskStatusUpdateEvent` frames over Server-Sent Events until a terminal state (`completed`, `failed`, `canceled`).

## Server stack

```
uvicorn (asgi)                    ← process runner
  └─ Starlette app                ← built by a2a-sdk
       ├─ CORSMiddleware
       └─ A2AStarletteApplication ← Agent Card + JSON-RPC routing
            └─ DefaultRequestHandler(
                 agent_executor = HermesAgentExecutor,   ← our bridge
                 task_store     = InMemoryTaskStore,
               )
```

`HermesAgentExecutor` is the only custom code. For each incoming task it:

1. Extracts text (and inline base64 images) from the A2A message parts.
2. Builds a fresh `run_agent.AIAgent` with model/provider resolved from `~/.hermes/config.yaml`.
3. Runs the agent in a thread pool, streaming deltas through a `queue.Queue`.
4. Converts each delta into either a `TaskArtifactUpdateEvent` (incremental text) or a `tool_call` artifact (tool invocation hint).
5. Emits a terminal `completed` status when the agent returns.

## Lazy coupling to hermes-agent

The A2A module is safe to import without `hermes-agent` installed — but it will raise `RuntimeError` the first time a task arrives. This lets consumers install the package and use only the A2A client helpers from the same process that would otherwise fail at import time.

`HERMES_AGENT_ROOT=/path/to/hermes-agent` adds the hermes checkout to `sys.path` at startup if you haven't pip-installed hermes-agent.

## Agent Card

Built from environment variables at startup:

| Env | Maps to |
|-----|---------|
| `AGENT_NAME` | `card.name` |
| `AGENT_DESCRIPTION` | `card.description` |
| `AGENT_SKILLS` (csv) | `card.skills[*]` |
| `A2A_PUBLIC_URL` | `card.url` (what callers use to reach you) |
| `AGENT_MODEL` | metadata hint only |

Streaming is always advertised (`capabilities.streaming = true`).

## Toolset scoping

`A2A_TOOLSETS=tool1,tool2` restricts which toolsets are loaded into the executing agent. Leave unset to mirror `hermes chat`. Useful for sandboxed A2A deployments that should only expose a safe subset (e.g. `A2A_TOOLSETS=hermes-acp` for filesystem-only).

## Client side

`hermes_adapter.a2a.client` provides `httpx`-based helpers with no SDK dependency:

```python
from hermes_adapter.a2a import a2a_discover, a2a_call, a2a_local_scan

print(a2a_discover("http://peer:9000"))
reply = a2a_call("http://peer:9000", "summarize the changes in this branch")
print(a2a_local_scan(host="localhost", port_start=9000, port_end=9010))
```

Streaming (`tasks/sendSubscribe`) is auto-selected when the peer's Agent Card advertises `capabilities.streaming = true`. Override with `stream=True/False`.

## Exposing the A2A client inside Hermes

If you want a local `hermes-agent` session to use these helpers as tools, register them from a hermes-agent plugin:

```python
from tools.registry import registry
from hermes_adapter.a2a import register_hermes_tools

register_hermes_tools(registry)
```

This adds `a2a_discover`, `a2a_call`, and `a2a_local_scan` to the local agent's toolbox.
