#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# hermes-adapter — local Hermes agent + Cloudflare quick tunnel
#                  (Hermes Studio "Local via Tunnel" mode)
#
# What you get:
#   * 1× hermes-adapter container running the unified gateway
#       /v1/*                  — OpenAI-compat (Studio chat)
#       POST /                 — A2A JSON-RPC  (Akela)
#       GET  /.well-known/...  — A2A discovery (Akela)
#       /ws/*                  — workspace API (Studio file ops)
#   * 1× filebrowser sidecar at /files/
#   * 1× Traefik (HTTP only — TLS is provided by cloudflared)
#   * 1× cloudflared quick tunnel pointing at Traefik
#
# The tunnel URL is EPHEMERAL — Cloudflare regenerates it each time
# cloudflared restarts. Keep this stack running while you work; tear
# down with ``docker compose down`` from $INSTALL_ROOT.
#
# Docker is REQUIRED. If it's missing, this script aborts (we don't
# auto-install Docker on a developer laptop). Install Docker Desktop
# (macOS / Windows) or follow https://docs.docker.com/engine/install/.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-local-tunnel.sh | bash
#
# Flags:
#   --provider     OPTIONAL. Provider env var family the key sets.
#                  One of: minimax | openai | anthropic | openrouter |
#                  together | groq | google. Default: minimax.
#   --provider-key OPTIONAL. Initial provider API key. If omitted you'll
#                  edit it post-install in $INSTALL_ROOT/secrets.env.
#   --studio-url   OPTIONAL. CORS origin. Default: https://hermes-studio.com
#
# Env overrides:
#   INSTALL_ROOT   Default: $HOME/.hermes-tunnel
#   ADAPTER_IMAGE  Default: ghcr.io/balaji-embedcentrum/hermes-adapter:latest
# ---------------------------------------------------------------------------

set -euo pipefail

# ── Color helpers ──────────────────────────────────────────────────────────
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_CYAN=$'\033[36m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_CYAN=""
fi
say()  { printf "%s▸ %s%s\n" "$C_BOLD" "$1" "$C_RESET"; }
ok()   { printf "%s✓ %s%s\n" "$C_GREEN" "$1" "$C_RESET"; }
warn() { printf "%s⚠ %s%s\n" "$C_YELLOW" "$1" "$C_RESET"; }
die()  { printf "%s✗ %s%s\n" "$C_RED" "$1" "$C_RESET" >&2; exit 1; }

# ── Docker required, no auto-install ───────────────────────────────────────
command -v docker >/dev/null 2>&1 || die "Docker not found. Install Docker Desktop (https://docs.docker.com/get-docker/) and re-run."
docker compose version >/dev/null 2>&1 || die "Docker compose plugin not found. Update Docker Desktop or install the v2 compose plugin."
docker info >/dev/null 2>&1 || die "Docker daemon not running. Start Docker Desktop / dockerd and re-run."
ok "$(docker --version | cut -d, -f1) — daemon healthy"

# ── Args ────────────────────────────────────────────────────────────────────
PROVIDER="minimax"
PROVIDER_KEY=""
STUDIO_URL="${STUDIO_URL:-https://hermes-studio.com}"

while [ $# -gt 0 ]; do
  case "$1" in
    --provider)      PROVIDER="$2";     shift 2 ;;
    --provider-key)  PROVIDER_KEY="$2"; shift 2 ;;
    --studio-url)    STUDIO_URL="$2";   shift 2 ;;
    -h|--help)       sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown flag: $1 (try --help)" ;;
  esac
done

case "$PROVIDER" in
  minimax)    PROVIDER_KEY_ENV="MINIMAX_API_KEY" ;;
  openai)     PROVIDER_KEY_ENV="OPENAI_API_KEY" ;;
  anthropic)  PROVIDER_KEY_ENV="ANTHROPIC_API_KEY" ;;
  openrouter) PROVIDER_KEY_ENV="OPENROUTER_API_KEY" ;;
  together)   PROVIDER_KEY_ENV="TOGETHER_API_KEY" ;;
  groq)       PROVIDER_KEY_ENV="GROQ_API_KEY" ;;
  google)     PROVIDER_KEY_ENV="GEMINI_API_KEY" ;;
  *) die "unknown provider: $PROVIDER" ;;
esac

# ── Config ──────────────────────────────────────────────────────────────────
INSTALL_ROOT="${INSTALL_ROOT:-$HOME/.hermes-tunnel}"
ADAPTER_IMAGE="${ADAPTER_IMAGE:-ghcr.io/balaji-embedcentrum/hermes-adapter:latest}"

# ── Folders ─────────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_ROOT/agent-data" "$INSTALL_ROOT/workspaces"
ok "install root: $INSTALL_ROOT"

# ── Build or pull the adapter image ────────────────────────────────────────
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" &>/dev/null && pwd)"
REPO_ROOT=""
[ -f "$SCRIPT_DIR/../Dockerfile" ] && REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
if [ -n "$REPO_ROOT" ]; then
  ADAPTER_IMAGE="hermes-adapter:local"
  say "building adapter from $REPO_ROOT → $ADAPTER_IMAGE"
  docker build -t "$ADAPTER_IMAGE" "$REPO_ROOT"
else
  say "pulling $ADAPTER_IMAGE"
  docker pull "$ADAPTER_IMAGE"
fi
docker pull traefik:latest
docker pull filebrowser/filebrowser:latest
docker pull cloudflare/cloudflared:latest

# ── Bearer key + filebrowser password (preserved across reinstalls) ────────
if [ -f "$INSTALL_ROOT/.bearer-key" ]; then
  BEARER_KEY="$(cat "$INSTALL_ROOT/.bearer-key")"
  warn "reusing existing bearer key"
else
  BEARER_KEY="$(openssl rand -hex 32)"
  echo "$BEARER_KEY" > "$INSTALL_ROOT/.bearer-key"
  chmod 600 "$INSTALL_ROOT/.bearer-key"
  ok "bearer key generated"
fi
if [ -f "$INSTALL_ROOT/.fb-password" ]; then
  FB_PASSWORD="$(cat "$INSTALL_ROOT/.fb-password")"
else
  FB_PASSWORD="$(openssl rand -hex 12)"
  echo "$FB_PASSWORD" > "$INSTALL_ROOT/.fb-password"
  chmod 600 "$INSTALL_ROOT/.fb-password"
fi

# ── secrets.env (provider key) ─────────────────────────────────────────────
SECRETS_FILE="$INSTALL_ROOT/secrets.env"
if [ ! -f "$SECRETS_FILE" ]; then
  cat > "$SECRETS_FILE" <<EOF
# Provider keys for the agent. Edit and ``docker compose restart agent``
# from $INSTALL_ROOT after changes.
${PROVIDER_KEY_ENV}=${PROVIDER_KEY}
EOF
  chmod 600 "$SECRETS_FILE"
  if [ -z "$PROVIDER_KEY" ]; then
    warn "no --provider-key given; edit $SECRETS_FILE before chatting"
  fi
fi

# ── docker-compose.yml ─────────────────────────────────────────────────────
# Two-step bring-up because the agent's A2A_PUBLIC_URL needs to advertise
# the cloudflared tunnel URL (otherwise A2A clients following the agent
# card discover ``localhost`` and fail). We start cloudflared first,
# capture the URL from its logs, then start the rest.
COMPOSE="$INSTALL_ROOT/docker-compose.yml"
FB_HASH="$(openssl passwd -apr1 "$FB_PASSWORD" | sed 's/\$/$$/g')"

cat > "$COMPOSE" <<YAML
name: hermes-tunnel

services:
  # cloudflared starts first (no dependencies on agent/traefik so the
  # quick-tunnel assignment is independent). It logs a
  # https://<random>.trycloudflare.com URL we can grep for.
  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    container_name: hermes-tunnel-cloudflared
    command: tunnel --no-autoupdate --url http://traefik:80
    networks: [tunnel]

  traefik:
    image: traefik:latest
    restart: unless-stopped
    container_name: hermes-tunnel-traefik
    environment:
      DOCKER_API_VERSION: "1.43"
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      # Plain HTTP only — TLS is terminated by cloudflared on the
      # tunnel edge, not by us. No LE.
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks: [tunnel]

  agent:
    image: $ADAPTER_IMAGE
    restart: unless-stopped
    container_name: hermes-tunnel-agent
    command: ["gateway"]
    env_file: ./secrets.env
    environment:
      A2A_HOST: 0.0.0.0
      A2A_PORT: 9001
      A2A_KEY: \${BEARER_KEY}
      # Set to the cloudflared URL after first boot (see set-tunnel-url
      # script below). Without it, A2A agent cards advertise the wrong
      # URL.
      A2A_PUBLIC_URL: \${TUNNEL_URL:-}
      API_SERVER_KEY: \${BEARER_KEY}
      HERMES_HOME: /root/.hermes
      HERMES_AGENT_ROOT: /opt/hermes
      HERMES_WORKSPACE_DIR: /opt/workspaces
      AGENT_NAME: primary
      HERMES_ADAPTER_CORS_ORIGINS: \${STUDIO_URL}
    volumes:
      - ./agent-data:/root/.hermes
      - ./workspaces:/opt/workspaces
    networks: [tunnel]
    labels:
      - traefik.enable=true
      - traefik.http.routers.agent.rule=PathPrefix(\`/\`)
      - traefik.http.routers.agent.priority=10
      - traefik.http.routers.agent.entrypoints=web
      - traefik.http.routers.agent.service=agent
      - traefik.http.services.agent.loadbalancer.server.port=9001

  filebrowser:
    image: filebrowser/filebrowser:latest
    restart: unless-stopped
    container_name: hermes-tunnel-files
    user: "0:0"
    command:
      - --noauth
      - --root=/srv
      - --baseurl=/files
      - --address=0.0.0.0
      - --port=80
    volumes:
      - ./agent-data:/srv
    networks: [tunnel]
    labels:
      - traefik.enable=true
      - traefik.http.routers.files.rule=PathPrefix(\`/files\`)
      - traefik.http.routers.files.priority=20
      - traefik.http.routers.files.entrypoints=web
      - traefik.http.routers.files.service=files
      - traefik.http.routers.files.middlewares=files-auth
      - traefik.http.services.files.loadbalancer.server.port=80
      - "traefik.http.middlewares.files-auth.basicauth.users=admin:$FB_HASH"

networks:
  tunnel:
    driver: bridge
YAML

cat > "$INSTALL_ROOT/.env" <<EOF
BEARER_KEY=$BEARER_KEY
FB_PASSWORD=$FB_PASSWORD
STUDIO_URL=$STUDIO_URL
TUNNEL_URL=
EOF
chmod 600 "$INSTALL_ROOT/.env"
( cd "$INSTALL_ROOT" && docker compose config >/dev/null )
ok "wrote $COMPOSE"

# ── Phase 1: bring up cloudflared, wait for the tunnel URL ─────────────────
say "starting cloudflared (waiting for tunnel URL)"
( cd "$INSTALL_ROOT" && docker compose up -d cloudflared )

TUNNEL_URL=""
for _ in $(seq 1 30); do
  TUNNEL_URL="$(docker logs hermes-tunnel-cloudflared 2>&1 \
    | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' \
    | head -n1 || true)"
  [ -n "$TUNNEL_URL" ] && break
  sleep 2
done

if [ -z "$TUNNEL_URL" ]; then
  warn "couldn't extract tunnel URL from cloudflared logs after 60s"
  warn "starting the rest of the stack anyway — A2A_PUBLIC_URL will be empty"
  warn "run 'docker logs hermes-tunnel-cloudflared' to find your URL manually"
else
  ok "tunnel URL: $TUNNEL_URL"
  # Persist the URL in .env so the agent picks it up via A2A_PUBLIC_URL.
  sed -i.bak "s|^TUNNEL_URL=.*|TUNNEL_URL=$TUNNEL_URL|" "$INSTALL_ROOT/.env" \
    && rm -f "$INSTALL_ROOT/.env.bak"
fi

# ── Phase 2: bring up everything else ──────────────────────────────────────
say "starting agent + filebrowser + traefik"
( cd "$INSTALL_ROOT" && docker compose up -d )
sleep 3
( cd "$INSTALL_ROOT" && docker compose ps )

# ── Print next steps ───────────────────────────────────────────────────────
cat <<EOF

${C_GREEN}${C_BOLD}═══ Local Hermes + Cloudflare tunnel installed ═══${C_RESET}

Tunnel URL    ${C_CYAN}${TUNNEL_URL:-<see "docker logs hermes-tunnel-cloudflared">}${C_RESET}
Files         ${C_CYAN}${TUNNEL_URL:-<tunnel-url>}/files/${C_RESET}  (admin / $FB_PASSWORD)
Bearer key    ${C_CYAN}$INSTALL_ROOT/.bearer-key${C_RESET}
Install root  ${C_CYAN}$INSTALL_ROOT${C_RESET}
Provider key  ${C_CYAN}$SECRETS_FILE${C_RESET}  (${PROVIDER_KEY_ENV})

${C_BOLD}Use it from Hermes Studio${C_RESET}
  1. Open Studio → Choose Your Agent → Local via Tunnel
  2. Paste:
       URL    ${TUNNEL_URL:-<tunnel-url>}
       Key    $BEARER_KEY

${C_BOLD}Use it from Akela (A2A)${C_RESET}
       Card   ${TUNNEL_URL:-<tunnel-url>}/.well-known/agent.json
       RPC    ${TUNNEL_URL:-<tunnel-url>}/   (POST)
       Key    $BEARER_KEY

${C_BOLD}Smoke test${C_RESET}
  ${C_DIM}curl -sS -H "Authorization: Bearer $BEARER_KEY" ${TUNNEL_URL:-<tunnel-url>}/v1/health${C_RESET}

${C_BOLD}Manage${C_RESET}
  Stop      ${C_DIM}cd $INSTALL_ROOT && docker compose down${C_RESET}
  Logs      ${C_DIM}cd $INSTALL_ROOT && docker compose logs -f${C_RESET}
  ${C_YELLOW}NOTE${C_RESET}: the tunnel URL ROTATES every time cloudflared restarts.
        Re-run this script (or 'docker compose restart cloudflared' +
        re-read the URL) and re-paste into Studio.

EOF
