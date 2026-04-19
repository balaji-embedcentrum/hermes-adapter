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

# Folders
sudo mkdir -p /srv/hermes/{workspaces,hermes-home}
sudo chown -R "$USER":"$USER" /srv/hermes
cd /srv/hermes
```

## Step 2 — Write `/srv/hermes/.env`

hermes-agent is provider-agnostic — set only the key for the LLM provider you're actually using.

```bash
cat > /srv/hermes/.env <<'EOF'
# --- Model provider (pick one; uncomment what you use) ---
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
# GEMINI_API_KEY=...
# OPENROUTER_API_KEY=sk-or-...
# Self-hosted OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, ...)
# OPENAI_API_KEY=dummy
# OPENAI_BASE_URL=http://ollama:11434/v1

# --- Bearer token the adapter + every A2A endpoint require ---
A2A_KEY=replace-with-long-random-string

# --- Public URL your Studio / Akela UI will call (points at Traefik) ---
PUBLIC_API_HOST=api.your-domain.com

# --- Host paths (do not change unless you moved the folders above) ---
HERMES_WORKSPACE_DIR=/srv/hermes/workspaces
HERMES_HOME=/srv/hermes/hermes-home
EOF
chmod 600 /srv/hermes/.env
```

You also need to tell hermes which model to use by default. Edit `/srv/hermes/hermes-home/config.yaml`:

```yaml
model:
  default: anthropic/claude-sonnet-4.6   # or openai/gpt-5, google/gemini-2.0-flash,
                                         # openrouter/meta-llama/llama-3.1-70b, etc.
```

Whatever you pick here must match the key you set above.

## Step 3 — Write `/srv/hermes/docker-compose.yml`

This example runs **3 agents** (`alpha`, `beta`, `gamma`). Copy-paste the `hermes-agent-alpha` block and rename to add more — the template is identical.

```yaml
# /srv/hermes/docker-compose.yml
name: hermes-stack

x-hermes-agent-common: &hermes-agent-common
  image: noushermes/hermes-agent:latest
  restart: unless-stopped
  env_file: .env
  environment:
    A2A_HOST: 0.0.0.0
    A2A_PORT: 9000
    A2A_KEY: ${A2A_KEY}
  volumes:
    - ${HERMES_WORKSPACE_DIR}:/workspaces
    - ${HERMES_HOME}:/root/.hermes
  command: ["hermes-a2a"]

services:
  # --- One shared adapter for all agents ------------------------------------
  adapter:
    image: ghcr.io/balaji-embedcentrum/hermes-adapter:latest
    restart: unless-stopped
    env_file: .env
    environment:
      HERMES_ADAPTER_HOST: 0.0.0.0
      HERMES_ADAPTER_PORT: 8766
      HERMES_WORKSPACE_DIR: /workspaces
    volumes:
      - ${HERMES_WORKSPACE_DIR}:/workspaces
      - ${HERMES_HOME}:/root/.hermes
    command: ["hermes-adapter", "workspace"]    # A2A is inside each agent
    labels:
      - traefik.enable=true
      - traefik.http.routers.ws.rule=Host(`${PUBLIC_API_HOST}`) && PathPrefix(`/ws`)
      - traefik.http.services.ws.loadbalancer.server.port=8766

  # --- One agent container per persona --------------------------------------
  hermes-agent-alpha:
    <<: *hermes-agent-common
    container_name: hermes-agent-alpha
    environment:
      A2A_HOST: 0.0.0.0
      A2A_PORT: 9000
      A2A_KEY: ${A2A_KEY}
      AGENT_NAME: alpha
      AGENT_DESCRIPTION: Generic hermes agent (alpha)
    labels:
      - traefik.enable=true
      - traefik.http.routers.alpha.rule=Host(`${PUBLIC_API_HOST}`) && PathPrefix(`/a2a/alpha`)
      - traefik.http.routers.alpha.middlewares=alpha-strip
      - traefik.http.middlewares.alpha-strip.stripprefix.prefixes=/a2a/alpha
      - traefik.http.services.alpha.loadbalancer.server.port=9000

  hermes-agent-beta:
    <<: *hermes-agent-common
    container_name: hermes-agent-beta
    environment:
      A2A_HOST: 0.0.0.0
      A2A_PORT: 9000
      A2A_KEY: ${A2A_KEY}
      AGENT_NAME: beta
      AGENT_DESCRIPTION: Generic hermes agent (beta)
    labels:
      - traefik.enable=true
      - traefik.http.routers.beta.rule=Host(`${PUBLIC_API_HOST}`) && PathPrefix(`/a2a/beta`)
      - traefik.http.routers.beta.middlewares=beta-strip
      - traefik.http.middlewares.beta-strip.stripprefix.prefixes=/a2a/beta
      - traefik.http.services.beta.loadbalancer.server.port=9000

  hermes-agent-gamma:
    <<: *hermes-agent-common
    container_name: hermes-agent-gamma
    environment:
      A2A_HOST: 0.0.0.0
      A2A_PORT: 9000
      A2A_KEY: ${A2A_KEY}
      AGENT_NAME: gamma
      AGENT_DESCRIPTION: Generic hermes agent (gamma)
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

### To add more agents (up to 30+)

Copy one of the `hermes-agent-*` blocks and change four things: service key, `container_name`, `AGENT_NAME`, and the Traefik prefix (`/a2a/<name>`). Nothing else.

A tiny generator script helps:

```bash
cat > /srv/hermes/generate-agents.sh <<'EOF'
#!/usr/bin/env bash
set -e
for name in "$@"; do
cat <<YAML
  hermes-agent-${name}:
    <<: *hermes-agent-common
    container_name: hermes-agent-${name}
    environment:
      A2A_HOST: 0.0.0.0
      A2A_PORT: 9000
      A2A_KEY: \${A2A_KEY}
      AGENT_NAME: ${name}
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

# Example: print blocks for 30 agents
./generate-agents.sh agent-{01..30}
```

Paste the output under `services:` in `docker-compose.yml`.

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
| Add a 31st agent | Append a block to `docker-compose.yml`, `docker compose up -d` |
| Rotate the A2A key | Edit `.env`, `docker compose up -d` (recreates all) |

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
