# Architecture

`hermes-adapter` is deliberately small. It wraps two unrelated concerns in one pip package so projects built on `hermes-agent` don't have to fork the core.

## The brain / hand split

```
                ┌─────────────────────────┐
                │    hermes-agent         │   ← the brain
                │    (stock upstream)     │      LLM, tool loop, agent runtime
                └──────────┬──────────────┘
                           │ imported lazily by A2A server
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         hermes-adapter                               │
│                                                                     │
│   ┌────────────────────┐         ┌──────────────────────────────┐  │
│   │   A2A server       │         │   Workspace API              │  │
│   │   :9000 (uvicorn)  │         │   :8766 (aiohttp)            │  │
│   │                    │         │                              │  │
│   │   /.well-known/    │         │   /ws                         │  │
│   │   agent.json       │         │   /ws/{repo}/tree            │  │
│   │                    │         │   /ws/{repo}/file            │  │
│   │   POST /           │         │   /ws/{repo}/git/*           │  │
│   │   (JSON-RPC)       │         │   /ws/{repo}/symbols         │  │
│   └──────────┬─────────┘         └──────────────┬───────────────┘  │
│              │                                  │                   │
└──────────────┼──────────────────────────────────┼───────────────────┘
               │                                  │
               ▼                                  ▼
       remote A2A orchestrators          Hermes Studio / Akela web UIs
       (Vertex AI, LangGraph, …)         (file tree, editor, git)
```

## Why two services in one package?

They share the same operational shape (Python, async, long-running HTTP server, deployed next to hermes-agent) but are independent at runtime:

- Workspace API has **zero** imports from `hermes-agent`. Install `hermes-adapter` alone and it runs.
- A2A server lazily imports `run_agent` and `hermes_cli` — only needed when a task actually comes in. The module is safe to *import* without hermes-agent installed (so mixed deployments work).

This means the same `pip install hermes-adapter[a2a]` satisfies both a pure-workspace sidecar (e.g. Hermes Studio editor backend) and a full A2A bridge (e.g. Akela calling Hermes over the standard protocol).

## Three deployment shapes

### Sidecar (default)

Two processes, two ports. Hermes Studio and Akela call both.

```
hermes-agent :8765 ──────┐
                         ├──→ clients
hermes-adapter :8766 ────┘
                :9000
```

### Plugin (shared port)

Use `mount_routes(app)` to attach workspace routes to an existing aiohttp application. Useful if you're already running an aiohttp server and want a single port.

```python
from aiohttp import web
from hermes_adapter.workspace.mount import mount_routes

app = web.Application()
mount_routes(app)
```

### Library

Skip the server entirely. Import the A2A client helpers from any Python code:

```python
from hermes_adapter.a2a import a2a_discover, a2a_call

print(a2a_discover("http://peer.example.com:9000"))
reply = a2a_call("http://peer.example.com:9000", "summarize the repo")
```

## What this package does NOT do

- It does not re-implement hermes-agent. Agent responses always come from upstream.
- It does not ship persona configs, fleet orchestration, or LLM-provider wiring. Those live in projects that consume the adapter.
- It does not authenticate users. Put it behind a proxy that does, or set `A2A_KEY` for a simple bearer check on the A2A endpoint.

## Security posture

- `/ws/*` runs git and filesystem operations as the process user. Deploy it behind an authenticating proxy; never expose it unauthenticated to the public internet.
- Path traversal is rejected by `resolve_safe_path()` — every `path=` parameter is normalized and checked against the workspace root.
- When `HERMES_WORKSPACE_DIR` ends in `/active`, the repo finder refuses to scan sibling directories, enforcing per-user isolation via symlink rebinding.
