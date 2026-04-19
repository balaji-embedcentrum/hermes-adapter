# VPS deployment (30+ hermes-agent containers)

## The short answer: install hermes-adapter **once**, not 30 times.

Here's why. Each thing you deploy does one job:

| Job | Who does it | How many copies |
|---|---|---|
| Run the LLM / agent loop | `hermes-agent` container | **one per agent** (30+) |
| Speak A2A (chat over JSON-RPC) | `hermes-agent` itself via `hermes-a2a` | **already inside every agent container** — no extra work |
| Serve files + git + Sylang symbols to Hermes Studio | `hermes-adapter` workspace API | **one, shared** |

So on your VPS you end up with:

```
                     ┌──────────────────────────────┐
                     │  Traefik / Caddy (reverse    │
                     │  proxy + TLS)                │
                     └────────┬─────────────────────┘
                              │
       ┌──────────────────────┼──────────────────────────────┐
       │                      │                              │
       ▼                      ▼                              ▼
 Hermes Studio       hermes-adapter (1×)           hermes-agent-{01..30}
  (Next.js)          :8766 workspace API           each on :9000 A2A
                                                   (via hermes-a2a)
                              │                              │
                              └─────── shared /workspaces ───┘
                                           volume
```

One container answers filesystem/git questions for the UI. Thirty containers each answer chat questions for their own persona. You do NOT put the adapter inside every agent container.

---

## What you'll end up with on the VPS

- `/srv/hermes/docker-compose.yml` — the whole stack
- `/srv/hermes/.env` — secrets
- `/srv/hermes/workspaces/` — shared repo storage every container mounts
- `/srv/hermes/hermes-home/` — shared `~/.hermes/` directory

Everything is in one `docker compose up` — no per-container setup once it's written.

---

## Step 1 — VPS prep (one time)

SSH into the VPS as root or a sudo user.

```bash
# Docker + compose
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # log out/in after this

# Folders: one shared workspace volume, one folder per agent's config
sudo mkdir -p /srv/hermes/workspaces
sudo mkdir -p /srv/hermes-agents/{alpha,beta,gamma}    # add more here as you scale
sudo chown -R "$USER":"$USER" /srv/hermes /srv/hermes-agents
cd /srv/hermes
```

**Why two folders.** `/srv/hermes/workspaces` holds every agent's repos — shared, because any agent can read/write any repo. `/srv/hermes-agents/<name>/` holds **that agent's** `.env` and `config.yaml` — private, because alpha uses Claude while beta uses a local Llama.

## Step 2 — Two tiers of config: stack-level vs per-agent

### 2a. Stack-level `/srv/hermes/.env` — only what's truly shared

The adapter does NOT need any model config. This file holds:

- `A2A_KEY` — bearer token every caller must present (shared so Studio only has to know one token)
- `PUBLIC_API_HOST` — the hostname Traefik serves on
- Path variables

```bash
cat > /srv/hermes/.env <<'EOF'
# Bearer token that every caller (Studio, Akela) presents to reach the agents
A2A_KEY=replace-with-long-random-string

# Public URL your Studio / Akela UI will call. Points at Traefik.
PUBLIC_API_HOST=api.your-domain.com

# Host path where every agent's repos live (shared volume)
HERMES_WORKSPACE_DIR=/srv/hermes/workspaces

# Parent of per-agent HERMES_HOME folders
HERMES_AGENTS_ROOT=/srv/hermes-agents
EOF
chmod 600 /srv/hermes/.env
```

**No model keys here.** They go in the per-agent files below.

### 2b. Per-agent `.env` + `config.yaml` — each agent picks its own model

Each agent gets its own folder under `/srv/hermes-agents/`. That folder is mounted into the container as `/root/.hermes`. Inside:

- `.env` holds the LLM provider key **for this agent only**
- `config.yaml` picks **this agent's** default model

**alpha — Claude Sonnet (code review)**
```bash
cat > /srv/hermes-agents/alpha/.env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
EOF
chmod 600 /srv/hermes-agents/alpha/.env

cat > /srv/hermes-agents/alpha/config.yaml <<'EOF'
model:
  default: anthropic/claude-sonnet-4.6
EOF
```

**beta — Llama 3.1 70B via OpenRouter (fast triage)**
```bash
cat > /srv/hermes-agents/beta/.env <<'EOF'
OPENROUTER_API_KEY=sk-or-...
EOF
chmod 600 /srv/hermes-agents/beta/.env

cat > /srv/hermes-agents/beta/config.yaml <<'EOF'
model:
  default: openrouter/meta-llama/llama-3.1-70b-instruct
EOF
```

**gamma — Gemini (research)**
```bash
cat > /srv/hermes-agents/gamma/.env <<'EOF'
GEMINI_API_KEY=...
EOF
chmod 600 /srv/hermes-agents/gamma/.env

cat > /srv/hermes-agents/gamma/config.yaml <<'EOF'
model:
  default: google/gemini-2.0-flash
EOF
```

Each agent is now isolated: alpha's Anthropic key cannot be used by beta, and beta's Llama choice doesn't leak into gamma.

## Step 3 — Write `/srv/hermes/docker-compose.yml`

This example runs **3 agents** (`alpha`, `beta`, `gamma`). Copy-paste the `hermes-agent-alpha` block and rename to add more — the template is identical.

```yaml
# /srv/hermes/docker-compose.yml
name: hermes-stack

# Common bits for every agent. Each agent overrides HERMES_HOME + env_file
# so it uses its own model + its own provider key.
x-hermes-agent-common: &hermes-agent-common
  image: noushermes/hermes-agent:latest
  restart: unless-stopped
  environment:
    A2A_HOST: 0.0.0.0
    A2A_PORT: 9000
    A2A_KEY: ${A2A_KEY}
  command: ["hermes-a2a"]

services:
  # --- One shared adapter for all agents (no model config) ------------------
  adapter:
    image: ghcr.io/balaji-embedcentrum/hermes-adapter:latest
    restart: unless-stopped
    environment:
      HERMES_ADAPTER_HOST: 0.0.0.0
      HERMES_ADAPTER_PORT: 8766
      HERMES_WORKSPACE_DIR: /workspaces
    volumes:
      - ${HERMES_WORKSPACE_DIR}:/workspaces
    command: ["hermes-adapter", "workspace"]    # A2A lives in each agent
    environment:
      HERMES_ADAPTER_HOST: 0.0.0.0
      HERMES_ADAPTER_PORT: 8766
      HERMES_WORKSPACE_DIR: /workspaces
      # Origins allowed to call the adapter from a user's browser.
      # Add every Studio / Akela UI that your users load.
      HERMES_ADAPTER_CORS_ORIGINS: ${HERMES_ADAPTER_CORS_ORIGINS:-https://studio.example.com}
    labels:
      - traefik.enable=true
      - traefik.http.routers.ws.rule=Host(`${PUBLIC_API_HOST}`) && PathPrefix(`/ws`)
      - traefik.http.services.ws.loadbalancer.server.port=8766

  # --- alpha: Claude Sonnet, code review ------------------------------------
  hermes-agent-alpha:
    <<: *hermes-agent-common
    container_name: hermes-agent-alpha
    env_file: ${HERMES_AGENTS_ROOT}/alpha/.env          # its own API key
    environment:
      A2A_HOST: 0.0.0.0
      A2A_PORT: 9000
      A2A_KEY: ${A2A_KEY}
      AGENT_NAME: alpha
      AGENT_DESCRIPTION: Code review (Claude Sonnet)
    volumes:
      - ${HERMES_WORKSPACE_DIR}:/workspaces
      - ${HERMES_AGENTS_ROOT}/alpha:/root/.hermes        # its own config.yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.alpha.rule=Host(`${PUBLIC_API_HOST}`) && PathPrefix(`/a2a/alpha`)
      - traefik.http.routers.alpha.middlewares=alpha-strip
      - traefik.http.middlewares.alpha-strip.stripprefix.prefixes=/a2a/alpha
      - traefik.http.services.alpha.loadbalancer.server.port=9000

  # --- beta: Llama 3.1 via OpenRouter, fast triage --------------------------
  hermes-agent-beta:
    <<: *hermes-agent-common
    container_name: hermes-agent-beta
    env_file: ${HERMES_AGENTS_ROOT}/beta/.env
    environment:
      A2A_HOST: 0.0.0.0
      A2A_PORT: 9000
      A2A_KEY: ${A2A_KEY}
      AGENT_NAME: beta
      AGENT_DESCRIPTION: Fast triage (Llama 3.1 via OpenRouter)
    volumes:
      - ${HERMES_WORKSPACE_DIR}:/workspaces
      - ${HERMES_AGENTS_ROOT}/beta:/root/.hermes
    labels:
      - traefik.enable=true
      - traefik.http.routers.beta.rule=Host(`${PUBLIC_API_HOST}`) && PathPrefix(`/a2a/beta`)
      - traefik.http.routers.beta.middlewares=beta-strip
      - traefik.http.middlewares.beta-strip.stripprefix.prefixes=/a2a/beta
      - traefik.http.services.beta.loadbalancer.server.port=9000

  # --- gamma: Gemini, research ----------------------------------------------
  hermes-agent-gamma:
    <<: *hermes-agent-common
    container_name: hermes-agent-gamma
    env_file: ${HERMES_AGENTS_ROOT}/gamma/.env
    environment:
      A2A_HOST: 0.0.0.0
      A2A_PORT: 9000
      A2A_KEY: ${A2A_KEY}
      AGENT_NAME: gamma
      AGENT_DESCRIPTION: Research (Gemini)
    volumes:
      - ${HERMES_WORKSPACE_DIR}:/workspaces
      - ${HERMES_AGENTS_ROOT}/gamma:/root/.hermes
    labels:
      - traefik.enable=true
      - traefik.http.routers.gamma.rule=Host(`${PUBLIC_API_HOST}`) && PathPrefix(`/a2a/gamma`)
      - traefik.http.routers.gamma.middlewares=gamma-strip
      - traefik.http.middlewares.gamma-strip.stripprefix.prefixes=/a2a/gamma
      - traefik.http.services.gamma.loadbalancer.server.port=9000

  # --- Reverse proxy / TLS --------------------------------------------------
  traefik:
    image: traefik:v3.1
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.websecure.address=:443
      - --certificatesresolvers.le.acme.email=you@example.com
      - --certificatesresolvers.le.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.le.acme.tlschallenge=true
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /srv/hermes/letsencrypt:/letsencrypt
```

The key lines to notice:
- `adapter` has **no** `env_file` and **no** model env vars — it's filesystem-only
- Each `hermes-agent-*` has its own `env_file` and mounts its own `HERMES_HOME` at `/root/.hermes`
- `A2A_KEY` is pulled from the shared stack env (bearer is the same for every agent)

### To add more agents (up to 30+)

Copy one of the `hermes-agent-*` blocks and change four things: service key, `container_name`, `AGENT_NAME`, and the Traefik prefix (`/a2a/<name>`). Nothing else.

A tiny generator script helps:

```bash
cat > /srv/hermes/generate-agents.sh <<'EOF'
#!/usr/bin/env bash
# Usage: ./generate-agents.sh alpha beta gamma ...
# Also creates the per-agent config folder if missing.
set -e
for name in "$@"; do
  mkdir -p /srv/hermes-agents/"$name"
  [ -f /srv/hermes-agents/"$name"/.env ] || touch /srv/hermes-agents/"$name"/.env
  [ -f /srv/hermes-agents/"$name"/config.yaml ] || cat > /srv/hermes-agents/"$name"/config.yaml <<YAML
model:
  default: anthropic/claude-sonnet-4.6    # edit to the model this agent should use
YAML
cat <<YAML
  hermes-agent-${name}:
    <<: *hermes-agent-common
    container_name: hermes-agent-${name}
    env_file: \${HERMES_AGENTS_ROOT}/${name}/.env
    environment:
      A2A_HOST: 0.0.0.0
      A2A_PORT: 9000
      A2A_KEY: \${A2A_KEY}
      AGENT_NAME: ${name}
    volumes:
      - \${HERMES_WORKSPACE_DIR}:/workspaces
      - \${HERMES_AGENTS_ROOT}/${name}:/root/.hermes
    labels:
      - traefik.enable=true
      - traefik.http.routers.${name}.rule=Host(\`\${PUBLIC_API_HOST}\`) && PathPrefix(\`/a2a/${name}\`)
      - traefik.http.routers.${name}.middlewares=${name}-strip
      - traefik.http.middlewares.${name}-strip.stripprefix.prefixes=/a2a/${name}
      - traefik.http.services.${name}.loadbalancer.server.port=9000
YAML
done
EOF
chmod +x /srv/hermes/generate-agents.sh

# Example: print blocks for 30 agents (also scaffolds their config folders)
./generate-agents.sh agent-{01..30}
```

Paste the output under `services:` in `docker-compose.yml`, then fill in each agent's `.env` + edit its `config.yaml` to pick the model for that agent.

## Step 4 — Bring the stack up

```bash
cd /srv/hermes
docker compose pull
docker compose up -d
docker compose ps
```

All containers should be `Up (healthy)` within 30 seconds.

## Step 5 — Verify

```bash
# Workspace API
curl -sf https://$PUBLIC_API_HOST/ws | jq

# Each agent's Agent Card
curl -sf -H "Authorization: Bearer $A2A_KEY" \
  https://$PUBLIC_API_HOST/a2a/alpha/.well-known/agent.json | jq
```

Both should return JSON. If they do — you're done.

## Step 6 — Point Hermes Studio at it

In Studio's `.env`:

```env
HERMES_ADAPTER_URL=https://api.your-domain.com
HERMES_A2A_BASE=https://api.your-domain.com/a2a
HERMES_A2A_KEY=<same as A2A_KEY above>
AVAILABLE_AGENTS=alpha,beta,gamma
```

Studio calls `${HERMES_ADAPTER_URL}/ws/<repo>/*` for files, and `${HERMES_A2A_BASE}/<agent>/` for chat. Switching the active agent is just changing one path segment on the client side.

---

## Day-2 operations

| Task | Command |
|---|---|
| Tail logs for one agent | `docker compose logs -f hermes-agent-alpha` |
| Restart a single agent | `docker compose restart hermes-agent-alpha` |
| Update everything | `docker compose pull && docker compose up -d` |
| Add a 31st agent | `./generate-agents.sh newname >> docker-compose.yml` → edit its `.env` + `config.yaml` → `docker compose up -d` |
| Change alpha's model | Edit `/srv/hermes-agents/alpha/config.yaml` → `docker compose restart hermes-agent-alpha` (no other agent affected) |
| Swap alpha from Claude to GPT | Replace `ANTHROPIC_API_KEY` with `OPENAI_API_KEY` in `/srv/hermes-agents/alpha/.env`, update its `config.yaml`, restart just that container |
| Rotate the shared A2A key | Edit `/srv/hermes/.env` `A2A_KEY`, `docker compose up -d` (recreates all agents) |

---

## Why NOT run the adapter in every agent container

If you put a workspace adapter inside each of the 30 containers:

- 30 copies of the same TTL cache, each walking the filesystem separately
- 30 endpoints that all do the same thing — Studio would have to pick one arbitrarily
- If one container is down, the workspace view from Studio changes
- 30× memory + 30× process startup time on deploy

One shared adapter + 30 tiny `hermes-a2a` processes is the right split.

---

## Resource sizing (rough)

Per `hermes-agent` container: ~300 MB RAM idle, ~800 MB active.
Adapter: ~80 MB RAM.
Traefik: ~30 MB.

For 30 agents: budget ~25 GB RAM and 4+ vCPU. If your VPS is smaller, scale agents down or use a single "router" agent that picks a persona via system prompt.
