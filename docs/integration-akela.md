# Integration: Akela

Akela is an orchestrator that calls remote agents over A2A. `hermes-adapter` makes any `hermes-agent` instance reachable from Akela with no code changes on Akela's side.

## Topology

```
Akela ──A2A JSON-RPC──→ :9000  hermes-adapter/a2a
                                      │  (lazy import)
                                      ▼
                                 hermes-agent
```

## 1. Run the adapter

```bash
export A2A_PORT=9000
export A2A_PUBLIC_URL=https://hermes.mycompany.com     # what Akela sees
export A2A_KEY=$(openssl rand -hex 32)                 # bearer token
export AGENT_NAME=hermes-coder
export AGENT_DESCRIPTION="Code review and repo analysis"
export AGENT_SKILLS=code_review,repo_analysis
hermes-adapter-a2a
```

## 2. Register with Akela

```yaml
# akela.yaml
agents:
  hermes-coder:
    url: https://hermes.mycompany.com:9000
    bearer_token: ${A2A_KEY}
    tags: [code, review]
```

Akela fetches `GET /.well-known/agent.json` to auto-discover skills. No SDK required on Akela's side — plain HTTP + JSON-RPC.

## 3. Calling from Akela

Akela uses whatever A2A client it already has. For reference, the equivalent Python looks like:

```python
from hermes_adapter.a2a import a2a_call

reply = a2a_call(
  "https://hermes.mycompany.com:9000",
  "Review PR #123 and summarize risks",
  session_id="akela-task-abc",
  bearer_token=os.environ["A2A_KEY"],
)
```

## 4. Image attachments

Akela attaches images as A2A `file` parts with `mimeType` starting with `image/` and base64-encoded bytes. The adapter converts them to Anthropic-style multimodal content blocks automatically:

```json
{
  "role":"user",
  "parts":[
    {"type":"text","text":"What's wrong with this diagram?"},
    {"kind":"file","file":{"mimeType":"image/png","bytes":"iVBORw0KGgo..."}}
  ]
}
```

## 5. Multi-tenant safety

- Each A2A call gets a fresh `AIAgent` per `sessionId` — no state leaks across tenants.
- Use `A2A_TOOLSETS=hermes-acp` (or a custom safe list) to restrict which tools the agent can use when invoked over A2A. This matters when Akela may route queries from untrusted sources.
- The adapter does not share memory between A2A and workspace routes — Akela only talks to `:9000`, never `:8766`.

## 6. CORS

`/` and `/.well-known/agent.json` serve `Access-Control-Allow-Origin: *` so browser-based Akela UIs can call the adapter directly during development. Put a reverse proxy in front to narrow this for production.
