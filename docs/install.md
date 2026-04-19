# Installing hermes-adapter — pick your path

Four scripts cover every combination of runtime (venv vs docker) and starting state (fresh vs already-installed hermes). All of them end in the same place: a working `hermes-adapter` with an `agents.yaml` manifest and a way to `up`/`down` the stack.

| | **Fresh install** (no hermes yet) | **Already have hermes** |
|---|---|---|
| **Python venv** | [`scripts/install.sh`](#1-venv-fresh) | [`scripts/install-adapter.sh`](#3-adapter-only) (autodetects venv) |
| **Docker** | [`scripts/install-docker.sh`](#2-docker-fresh) | [`scripts/install-adapter.sh`](#3-adapter-only) (autodetects docker) |

One command for each path is below.

---

## 1. venv, fresh
For a laptop or a VPS where you don't have hermes yet and you're happy with a Python install.

```bash
curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install.sh | bash
```

What it does:
- Picks Python 3.11+, creates `~/.hermes-venv`
- `pip install`s stock `hermes-agent` + `hermes-adapter` with `[a2a]` extras
- Runs `hermes-adapter init` to scaffold `~/.hermes-adapter/agents.yaml` with a fresh bearer token and `https://hermes-studio.com` allowed via CORS

Then:
```bash
source ~/.hermes-venv/bin/activate
hermes-adapter agent add alpha --model anthropic/claude-sonnet-4.6 --prompt-key
hermes-adapter up
```

Full walkthrough: [deploy-local.md](deploy-local.md).

---

## 2. docker, fresh
For a VPS where you prefer container isolation.

```bash
curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-docker.sh | bash
```

What it does:
- Checks Docker + Compose v2 are installed
- Creates `/srv/hermes-adapter/` (or the path in `HERMES_ROOT`)
- Pulls `ghcr.io/balaji-embedcentrum/hermes-adapter:latest` (falls back to a local build if run from a repo clone)
- Runs a throwaway adapter container to scaffold `agents.yaml`
- Drops a `hermesctl` wrapper that runs any `hermes-adapter` CLI command inside the image

Then:
```bash
cd /srv/hermes-adapter
./hermesctl agent add alpha --model anthropic/claude-sonnet-4.6 --key sk-ant-...
./hermesctl agent add beta  --model openai/gpt-5 --key sk-...

./hermesctl compose generate -o docker-compose.yml
docker compose up -d
docker compose ps
```

`hermes-adapter compose generate` reads your `agents.yaml` and emits a complete `docker-compose.yml` with one service per agent — same declarative config as venv mode, just a different runtime. Front with Caddy/Traefik for TLS.

Multi-tenant platform scale (30+ agents, per-user isolation) is covered in [deploy-vps.md](deploy-vps.md).

---

## 3. adapter only (you already have hermes)
For any host that already runs `hermes-agent` — laptop venv, VPS venv, or Docker. The script auto-detects which mode you're in.

```bash
curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-adapter.sh | bash
```

How it detects:

1. **Docker first** — if any running container's image contains `hermes-agent`, it uses Docker mode.
2. **Venv fallback** — checks `$HERMES_VENV`, `~/.hermes-venv`, and the directory containing the `hermes` command for an importable `run_agent` module.

Override with `HERMES_MODE=venv HERMES_VENV=/path/to/venv` or `HERMES_MODE=docker` if the detection guesses wrong.

### What happens in each mode

**Venv mode:**
- `pip install hermes-adapter[a2a]` into the SAME venv as existing hermes (no new venv)
- Runs `hermes-adapter init` if no manifest exists
- You add agents and `up` exactly like path #1

**Docker mode:**
- Pulls (or builds) the adapter image
- Writes `docker-compose.override.yml` that adds only the `adapter` service as a sidecar — your existing `docker-compose.yml` isn't touched
- Drops a `hermesctl` wrapper
- `docker compose up -d adapter` brings the adapter online alongside your existing hermes containers

---

## Which one should you pick?

```
Do you already have hermes running on this host?
├── No  → fresh install:  use path 1 (venv) or path 2 (docker)
└── Yes → adapter-only:   use path 3 (autodetects)

Are you on a laptop or small dev box?
└── path 1 is simplest — no Docker required

Are you on a VPS with other containerized services?
└── path 2 or path 3's docker branch integrates cleanly

Want to mix venv hermes with docker adapter (or vice versa)?
└── technically possible, but path 3 will pick one based on what it sees.
    You can force either with HERMES_MODE.
```

---

## After any install path

The things you get are always the same:

- `agents.yaml` — one declarative file listing every agent + model + port + HERMES_HOME
- `hermes-adapter agent add/remove/list` — manage that manifest
- `hermes-adapter up`/`down`/`status` (venv) or `docker compose up -d`/`down` (docker) — lifecycle
- `~/hermes-workspaces/` (or `/srv/hermes-adapter/workspaces/`) — where user repos live
- CORS allowlist for `https://hermes-studio.com` baked in — Studio's browser JS can reach the adapter directly

See [agent-sources.md](agent-sources.md) for how this fits into the bigger 3-tier picture.
