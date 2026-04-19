# A2A user guide

Run a hermes-agent instance as a standard A2A endpoint and call it from anywhere.

## 1. Install

```bash
pip install 'hermes-adapter[a2a]'
# hermes-agent must also be importable (pip-installed, or HERMES_AGENT_ROOT=<path>)
```

## 2. Configure

Copy `.env.example` to `.env` and edit at minimum:

```env
AGENT_NAME=my-agent
AGENT_DESCRIPTION=Summarizer + researcher
AGENT_SKILLS=summarize,research
A2A_PORT=9000
# Set a bearer token if the endpoint will be network-accessible
# A2A_KEY=long-random-string
# Public URL advertised in the Agent Card — required for cross-host callers
# A2A_PUBLIC_URL=https://agent.example.com
```

## 3. Start

```bash
hermes-adapter-a2a
# or, inside the combined adapter
hermes-adapter serve --a2a-port 9000
```

Open http://localhost:9000/.well-known/agent.json to confirm the Agent Card.

## 4. Call from another process

### Python (via this package's client)

```python
from hermes_adapter.a2a import a2a_discover, a2a_call

print(a2a_discover("http://localhost:9000"))
print(a2a_call("http://localhost:9000", "What changed in this repo today?"))
```

### curl

```bash
curl -s http://localhost:9000/.well-known/agent.json | jq

curl -s -X POST http://localhost:9000/ \
  -H 'content-type: application/json' \
  -H 'authorization: Bearer $A2A_KEY' \
  -d '{
        "jsonrpc":"2.0",
        "id":"req-1",
        "method":"tasks/send",
        "params":{
          "id":"task-1",
          "sessionId":"session-1",
          "message":{"role":"user","parts":[{"type":"text","text":"hello"}]}
        }
      }' | jq
```

### Any A2A-compatible orchestrator

Vertex AI Agent Engine, LangGraph A2A clients, CrewAI, etc. — just point them at the Agent Card URL.

## 5. Multi-turn conversations

Pass the same `session_id` (mapped to `contextId` in the A2A protocol) across calls:

```python
a2a_call(url, "My name is Alice", session_id="chat-42")
a2a_call(url, "What's my name?", session_id="chat-42")  # agent remembers
```

## 6. Restrict tool access

Set `A2A_TOOLSETS=hermes-acp` (or any csv list) to limit which toolsets the executing agent loads. Unset = same toolsets as `hermes chat`.

## 7. Discover peers

If you have several A2A agents on localhost, scan them:

```bash
python -c "from hermes_adapter.a2a import a2a_local_scan; print(a2a_local_scan())"
```

## Troubleshooting

- **`a2a-sdk is not installed`** → `pip install 'hermes-adapter[a2a]'`
- **`hermes-agent is not importable`** → either pip-install it or set `HERMES_AGENT_ROOT=/path/to/hermes-agent`
- **Empty responses** → check the agent is returning text; image-only responses aren't supported
- **Streaming hangs** → the server may not be advertising `capabilities.streaming`. Pass `stream=False` to `a2a_call` to force non-streaming
