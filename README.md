# hermes-adapter

> A sidecar adapter for [hermes-agent](https://github.com/NousResearch/hermes-agent) that exposes an **A2A (Agent-to-Agent) server** and a **workspace HTTP API** (filesystem, git, Sylang symbols) — without patching hermes-agent core.

Designed for projects like **Hermes Studio** and **Akela** that need hermes-agent compatibility but want to stay on stock upstream.

## Why

`hermes-agent` is the brain (LLM, tools, agent loop). Projects built on top often need two extra things that don't belong in the core:

1. **A2A server** — expose the agent over the standard Agent-to-Agent JSON-RPC protocol so orchestrators (Vertex AI, LangGraph, Akela) can discover and call it.
2. **Workspace HTTP API** — read/write files, run git commands, and deliver batched file contents for web IDEs. Pure filesystem + git, no LLM in the loop.

`hermes-adapter` packages both as a separate, pip-installable sidecar. Run it alongside stock `hermes-agent` — zero fork maintenance.

## Install — one command, pick your path

Four installers cover every combination of **runtime** (Python venv vs Docker) and **starting state** (fresh vs already-have-hermes). Full table: [docs/install.md](docs/install.md).

```bash
# 1. venv + fresh install (laptop / simple VPS)
curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install.sh | bash

# 2. docker + fresh install (VPS)
curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-docker.sh | bash

# 3 + 4. adapter only — you already have hermes (auto-detects venv vs docker)
curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-adapter.sh | bash
```

Or, if you want the package yourself:

```bash
pip install 'hermes-adapter[a2a]'   # requires hermes-agent importable at runtime for the A2A server
```

## Use

Each install path bootstraps `~/.hermes-adapter/agents.yaml`. After that:

```bash
# add each agent (repeat per model / provider / persona)
hermes-adapter agent add alpha --model anthropic/claude-sonnet-4.6 --prompt-key
hermes-adapter agent add beta  --model openrouter/meta-llama/llama-3.1-70b-instruct --prompt-key

# venv mode: supervisor runs workspace API + every agent under one parent process
hermes-adapter up                # Ctrl-C to stop
hermes-adapter up --detach       # background
hermes-adapter status
hermes-adapter down

# docker mode: generate compose from the same agents.yaml
hermes-adapter compose generate -o docker-compose.yml
docker compose up -d
```

Power-user commands (when you don't want the supervisor / no manifest yet):

```bash
hermes-adapter workspace         # just the workspace API
hermes-adapter a2a               # just the A2A server (requires hermes-agent)
hermes-adapter serve             # both from env vars, single-process
```

## Endpoints

### Workspace API (`:8766`)

All routes are plain HTTP — no LLM, no agent, just filesystem + git.

| Method | Path | Description |
|-------|------|-------------|
| `GET` | `/ws` | List workspace directories |
| `POST` | `/ws/activate` | Symlink `{user}` as the active workspace |
| `POST` | `/ws/deactivate` | Remove the active symlink |
| `POST` | `/ws/{repo}/init` | Clone repo (body: `{url, branch}`) or create empty (`{empty: true}`) |
| `GET` | `/ws/{repo}/tree?path=` | List directory (one level) |
| `GET` | `/ws/{repo}/file?path=` | Read file content |
| `POST` | `/ws/{repo}/file` | Write file (`{path, content}`) |
| `DELETE` | `/ws/{repo}/file?path=` | Delete file or directory |
| `GET` | `/ws/{repo}/git/status` | Porcelain status + ahead/behind |
| `POST` | `/ws/{repo}/git/commit` | Stage all + commit (`{message}`) |
| `POST` | `/ws/{repo}/git/push` | Push origin HEAD |
| `POST` | `/ws/{repo}/git/pull` | Pull --rebase |
| `POST` | `/ws/{repo}/git/pr` | `gh pr create` (`{title, body?, base?}`) |
| `GET` | `/ws/{repo}/git/log?limit=50` | Commit history |
| `GET` | `/ws/{repo}/git/files?commit=` | Files changed in commit |
| `GET` | `/ws/{repo}/symbols` | Batch-deliver all Sylang files |
| `POST` | `/ws/{repo}/symbols/invalidate` | Bust the symbols cache |

### A2A server (`:9000`)

| Method | Path | Description |
|-------|------|-------------|
| `GET` | `/.well-known/agent.json` | Agent Card |
| `POST` | `/` | JSON-RPC 2.0 (`tasks/send`, `tasks/sendSubscribe`) |

See [docs/workspace-api.md](docs/workspace-api.md) and [docs/a2a-architecture.md](docs/a2a-architecture.md) for details.

## Config

| Env var | Default | Description |
|---------|---------|-------------|
| `HERMES_WORKSPACE_DIR` | `/workspaces` | Root for workspace repos |
| `HERMES_ADAPTER_HOST` | `0.0.0.0` | Workspace bind host |
| `HERMES_ADAPTER_PORT` | `8766` | Workspace bind port |
| `A2A_HOST` | `0.0.0.0` | A2A bind host |
| `A2A_PORT` | `9000` | A2A bind port |
| `A2A_KEY` | *(unset)* | Optional Bearer token |
| `A2A_PUBLIC_URL` | `http://localhost:{port}` | URL in Agent Card |
| `AGENT_NAME` | `hermes-agent` | Agent Card name |
| `AGENT_DESCRIPTION` | *(auto)* | Agent Card description |
| `AGENT_SKILLS` | *(empty)* | Comma-separated skill names |
| `AGENT_MODEL` | *(empty)* | Model hint in Agent Card |

## Docker

```bash
docker compose up
```

Brings up `hermes-agent` on `:8765` and `hermes-adapter` on `:8766` (workspace) + `:9000` (A2A).

## Deployment guides

See [docs/install.md](docs/install.md) for the 4-path installer matrix, then pick the deployment tier that matches your setup:

- [docs/deploy-local.md](docs/deploy-local.md) — **tier 1:** user's laptop, hosted Studio calls `127.0.0.1:*` via CORS
- [docs/deploy-user-vps.md](docs/deploy-user-vps.md) — **tier 2:** user's own single-tenant VPS, Caddy + systemd
- [docs/deploy-vps.md](docs/deploy-vps.md) — **tier 3:** platform operator (hermes-studio.com) runs the multi-tenant fleet
- [docs/agent-sources.md](docs/agent-sources.md) — the three-tier mental model for Studio operators

## Integration

- [docs/integration-studio.md](docs/integration-studio.md) — Hermes Studio wiring
- [docs/integration-akela.md](docs/integration-akela.md) — Akela orchestrator

## Development

```bash
pip install -e '.[all]'
pytest
```

## License

MIT — see [LICENSE](LICENSE).
