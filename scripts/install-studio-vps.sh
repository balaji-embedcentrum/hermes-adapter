#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# hermes-adapter — single-agent VPS installer (Hermes Studio "Your VPS" mode)
#
# What you get:
#   * 1× hermes-adapter container running the unified gateway
#       /v1/*                  — OpenAI-compat (Studio chat)
#       POST /                 — A2A JSON-RPC  (Akela)
#       GET  /.well-known/...  — A2A discovery (Akela)
#       /ws/*                  — workspace API (Studio file ops)
#   * 1× filebrowser sidecar at /files/
#   * 1× Traefik with Let's Encrypt TLS
#
# DNS: ONE A record for $DOMAIN → this VPS. Path-based routing means no
# wildcard or per-service subdomain.
#
# After install, paste the URL + bearer key into Studio's "Your VPS" form
# (or Akela's agent registry) and you're live.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-studio-vps.sh \
#     | bash -s -- --domain agent.example.com --acme-email you@example.com
#
# Flags:
#   --domain       REQUIRED. Public hostname. DNS A record must already
#                  point at this host for Let's Encrypt HTTP-01 to succeed.
#   --acme-email   REQUIRED. Email for Let's Encrypt renewal notices.
#   --provider-key OPTIONAL. Initial provider API key. If omitted you'll
#                  edit it post-install in $INSTALL_ROOT/secrets.env.
#   --provider     OPTIONAL. Which provider env var the key sets.
#                  One of: minimax | openai | anthropic | openrouter |
#                  together | groq | google. Default: minimax.
#   --studio-url   OPTIONAL. CORS origin for browser requests.
#                  Default: https://hermes-studio.com.
#
# Env overrides:
#   INSTALL_ROOT   Install root (default: /srv/hermes-vps)
#   STATE_DIR      Persistent state dir (default: /var/lib/hermes-vps-state).
#                  Survives ``rm -rf $INSTALL_ROOT`` so LE certs + bearer
#                  key + filebrowser password aren't regenerated on every
#                  reinstall. Don't burn the LE rate limit (5/week per
#                  identifier) by deleting this casually.
#   ADAPTER_IMAGE  Adapter image tag
#                  (default: ghcr.io/balaji-embedcentrum/hermes-adapter:latest)
#   TRAEFIK_IMAGE  Traefik image (default: traefik:latest)
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

# ── Self-bootstrap: install Docker + clone repo if curl|bash on naked VPS ──
SUDO=""; [ "$(id -u)" = "0" ] || SUDO="sudo"
BOOTSTRAP_DIR="${BOOTSTRAP_DIR:-/opt/hermes-adapter}"
BOOTSTRAP_REPO="${BOOTSTRAP_REPO:-https://github.com/balaji-embedcentrum/hermes-adapter.git}"
BOOTSTRAP_REF="${BOOTSTRAP_REF:-main}"

_ensure_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return 0
  fi
  say "installing Docker Engine + compose plugin (via get.docker.com)"
  curl -fsSL https://get.docker.com | $SUDO sh
  command -v docker >/dev/null 2>&1 || die "Docker install failed"
  docker compose version >/dev/null 2>&1 || die "Docker compose plugin missing after install"
  ok "Docker $(docker --version | cut -d, -f1)"
}

_need_bootstrap() {
  local src="${BASH_SOURCE[0]:-}"
  [ -n "$src" ] && [ -f "$src" ] || return 0
  local dir
  dir="$(cd -- "$(dirname -- "$src")/.." &>/dev/null && pwd || echo "")"
  [ -n "$dir" ] && [ -f "$dir/Dockerfile" ] && [ -f "$dir/pyproject.toml" ] || return 0
  return 1
}

if _need_bootstrap; then
  if ! command -v git >/dev/null 2>&1; then
    say "installing git"
    $SUDO apt-get update -qq && $SUDO apt-get install -y --no-install-recommends git ca-certificates
  fi
  _ensure_docker
  say "cloning $BOOTSTRAP_REPO@$BOOTSTRAP_REF → $BOOTSTRAP_DIR"
  if [ -d "$BOOTSTRAP_DIR/.git" ]; then
    git -C "$BOOTSTRAP_DIR" fetch --depth=1 origin "$BOOTSTRAP_REF"
    git -C "$BOOTSTRAP_DIR" reset --hard "origin/$BOOTSTRAP_REF"
  else
    $SUDO mkdir -p "$(dirname "$BOOTSTRAP_DIR")"
    $SUDO chown "$USER:$USER" "$(dirname "$BOOTSTRAP_DIR")" 2>/dev/null || true
    git clone --depth=1 --branch "$BOOTSTRAP_REF" "$BOOTSTRAP_REPO" "$BOOTSTRAP_DIR"
  fi
  ok "bootstrap complete — re-exec'ing"
  exec "$BOOTSTRAP_DIR/scripts/install-studio-vps.sh" "$@"
fi
_ensure_docker

# ── Args ────────────────────────────────────────────────────────────────────
DOMAIN=""
ACME_EMAIL=""
PROVIDER="minimax"
PROVIDER_KEY=""
STUDIO_URL="${STUDIO_URL:-https://hermes-studio.com}"

while [ $# -gt 0 ]; do
  case "$1" in
    --domain)        DOMAIN="$2";       shift 2 ;;
    --acme-email)    ACME_EMAIL="$2";   shift 2 ;;
    --provider)      PROVIDER="$2";     shift 2 ;;
    --provider-key)  PROVIDER_KEY="$2"; shift 2 ;;
    --studio-url)    STUDIO_URL="$2";   shift 2 ;;
    -h|--help)       sed -n '2,55p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown flag: $1 (try --help)" ;;
  esac
done
[ -n "$DOMAIN" ]     || die "--domain is required"
[ -n "$ACME_EMAIL" ] || die "--acme-email is required"

# Map provider → env var name. Same table as install-fleet.sh's
# key_var_for_model and matches hermes_adapter/proxy/providers.py.
case "$PROVIDER" in
  minimax)    PROVIDER_KEY_ENV="MINIMAX_API_KEY" ;;
  openai)     PROVIDER_KEY_ENV="OPENAI_API_KEY" ;;
  anthropic)  PROVIDER_KEY_ENV="ANTHROPIC_API_KEY" ;;
  openrouter) PROVIDER_KEY_ENV="OPENROUTER_API_KEY" ;;
  together)   PROVIDER_KEY_ENV="TOGETHER_API_KEY" ;;
  groq)       PROVIDER_KEY_ENV="GROQ_API_KEY" ;;
  google)     PROVIDER_KEY_ENV="GEMINI_API_KEY" ;;
  *) die "unknown provider: $PROVIDER (try minimax|openai|anthropic|openrouter|together|groq|google)" ;;
esac

# ── Config ──────────────────────────────────────────────────────────────────
INSTALL_ROOT="${INSTALL_ROOT:-/srv/hermes-vps}"
STATE_DIR="${STATE_DIR:-/var/lib/hermes-vps-state}"
ADAPTER_IMAGE="${ADAPTER_IMAGE:-ghcr.io/balaji-embedcentrum/hermes-adapter:latest}"
TRAEFIK_IMAGE="${TRAEFIK_IMAGE:-traefik:latest}"

# ── DNS sanity ──────────────────────────────────────────────────────────────
say "checking DNS for $DOMAIN"
RESOLVED="$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -n1 || true)"
if [ -z "$RESOLVED" ]; then
  warn "$DOMAIN does not resolve yet — Let's Encrypt will fail until it does."
  warn "Add an A record pointing $DOMAIN at this VPS, then re-run."
  read -r -p "Continue anyway? [y/N] " yn
  [[ "$yn" =~ ^[Yy]$ ]] || die "aborted"
else
  ok "$DOMAIN → $RESOLVED"
fi

# ── Folders ─────────────────────────────────────────────────────────────────
$SUDO mkdir -p "$INSTALL_ROOT/agent-data" "$INSTALL_ROOT/workspaces"
$SUDO chown -R "$USER:$USER" "$INSTALL_ROOT"
ok "install root: $INSTALL_ROOT"

# ── Persistent state OUTSIDE install root ──────────────────────────────────
# Survives ``rm -rf $INSTALL_ROOT`` so reinstalls don't burn through Let's
# Encrypt rate limits or invalidate the bearer + filebrowser passwords.
$SUDO mkdir -p "$STATE_DIR/letsencrypt"
$SUDO chmod 700 "$STATE_DIR"
$SUDO touch "$STATE_DIR/letsencrypt/acme.json"
$SUDO chmod 600 "$STATE_DIR/letsencrypt/acme.json"

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
docker pull "$TRAEFIK_IMAGE"
docker pull filebrowser/filebrowser:latest

# ── Bearer key + filebrowser password (preserved across reinstalls) ────────
if [ -f "$STATE_DIR/.bearer-key" ]; then
  BEARER_KEY="$(cat "$STATE_DIR/.bearer-key")"
  warn "reusing existing bearer key from $STATE_DIR"
else
  BEARER_KEY="$(openssl rand -hex 32)"
  echo "$BEARER_KEY" | $SUDO tee "$STATE_DIR/.bearer-key" >/dev/null
  $SUDO chmod 600 "$STATE_DIR/.bearer-key"
  ok "bearer key generated"
fi
if [ -f "$STATE_DIR/.fb-password" ]; then
  FB_PASSWORD="$(cat "$STATE_DIR/.fb-password")"
else
  FB_PASSWORD="$(openssl rand -hex 12)"
  echo "$FB_PASSWORD" | $SUDO tee "$STATE_DIR/.fb-password" >/dev/null
  $SUDO chmod 600 "$STATE_DIR/.fb-password"
fi
$SUDO chown "$USER:$USER" "$STATE_DIR/.bearer-key" "$STATE_DIR/.fb-password"

# ── secrets.env (provider key) ─────────────────────────────────────────────
# Lives inside the install root so the user can edit it manually later.
# Compose env_file picks it up.
SECRETS_FILE="$INSTALL_ROOT/secrets.env"
if [ ! -f "$SECRETS_FILE" ]; then
  cat > "$SECRETS_FILE" <<EOF
# Provider keys for the agent. Edit as needed and ``docker compose restart agent``.
# The key var name must match what hermes-agent's runtime resolver reads
# for your chosen model — see hermes_adapter/proxy/providers.py.
${PROVIDER_KEY_ENV}=${PROVIDER_KEY}
EOF
  chmod 600 "$SECRETS_FILE"
  ok "wrote $SECRETS_FILE"
  if [ -z "$PROVIDER_KEY" ]; then
    warn "no --provider-key given; edit $SECRETS_FILE before chatting"
  fi
fi

# ── Stack-level .env (compose interpolation) ───────────────────────────────
cat > "$INSTALL_ROOT/.env" <<EOF
DOMAIN=$DOMAIN
ACME_EMAIL=$ACME_EMAIL
BEARER_KEY=$BEARER_KEY
FB_PASSWORD=$FB_PASSWORD
STATE_DIR=$STATE_DIR
STUDIO_URL=$STUDIO_URL
EOF
chmod 600 "$INSTALL_ROOT/.env"

# ── Filebrowser basic-auth hash (apr1) for Traefik middleware ──────────────
# $$ doubling escapes compose's variable interpolation so the hash makes
# it through verbatim into the label.
FB_HASH="$(openssl passwd -apr1 "$FB_PASSWORD" | sed 's/\$/$$/g')"

# ── docker-compose.yml ─────────────────────────────────────────────────────
COMPOSE="$INSTALL_ROOT/docker-compose.yml"
cat > "$COMPOSE" <<YAML
name: hermes-vps

services:
  traefik:
    image: $TRAEFIK_IMAGE
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    environment:
      DOCKER_API_VERSION: "1.43"
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.websecure.address=:443
      - --entrypoints.web.http.redirections.entrypoint.to=websecure
      - --entrypoints.web.http.redirections.entrypoint.scheme=https
      - --certificatesresolvers.le.acme.email=\${ACME_EMAIL}
      - --certificatesresolvers.le.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.le.acme.tlschallenge=true
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      # acme.json lives in STATE_DIR so it survives ``rm -rf $INSTALL_ROOT``.
      - \${STATE_DIR}/letsencrypt:/letsencrypt
    networks: [vps]

  agent:
    image: $ADAPTER_IMAGE
    restart: unless-stopped
    container_name: hermes-vps-agent
    command: ["gateway"]
    env_file: ./secrets.env
    environment:
      A2A_HOST: 0.0.0.0
      A2A_PORT: 9001
      A2A_KEY: \${BEARER_KEY}
      A2A_PUBLIC_URL: https://\${DOMAIN}
      API_SERVER_KEY: \${BEARER_KEY}
      HERMES_HOME: /root/.hermes
      HERMES_AGENT_ROOT: /opt/hermes
      HERMES_WORKSPACE_DIR: /opt/workspaces
      AGENT_NAME: primary
      HERMES_ADAPTER_CORS_ORIGINS: \${STUDIO_URL}
    volumes:
      - ./agent-data:/root/.hermes
      - ./workspaces:/opt/workspaces
    networks: [vps]
    labels:
      - traefik.enable=true
      # Catch-all router for everything that isn't /files. Routes /v1/*,
      # /ws/*, /.well-known/*, and POST / (A2A RPC) to the gateway.
      - traefik.http.routers.agent.rule=Host(\`\${DOMAIN}\`)
      - traefik.http.routers.agent.priority=10
      - traefik.http.routers.agent.entrypoints=websecure
      - traefik.http.routers.agent.tls.certresolver=le
      - traefik.http.routers.agent.service=agent
      - traefik.http.services.agent.loadbalancer.server.port=9001

  filebrowser:
    image: filebrowser/filebrowser:latest
    restart: unless-stopped
    container_name: hermes-vps-files
    user: "0:0"
    command:
      - --noauth
      - --root=/srv
      - --baseurl=/files
      - --address=0.0.0.0
      - --port=80
    volumes:
      # Browse the agent's hermes home — sessions, memory, persona,
      # config. NOT the user's GitHub repos under workspaces/, since
      # those are the agent's working directory and we want a clear
      # boundary.
      - ./agent-data:/srv
    networks: [vps]
    labels:
      - traefik.enable=true
      # Higher-priority router so /files wins over the catch-all agent.
      - traefik.http.routers.files.rule=Host(\`\${DOMAIN}\`) && PathPrefix(\`/files\`)
      - traefik.http.routers.files.priority=20
      - traefik.http.routers.files.entrypoints=websecure
      - traefik.http.routers.files.tls.certresolver=le
      - traefik.http.routers.files.service=files
      - traefik.http.routers.files.middlewares=files-auth
      - traefik.http.services.files.loadbalancer.server.port=80
      # Filebrowser runs --noauth; auth is enforced at the Traefik edge.
      - "traefik.http.middlewares.files-auth.basicauth.users=admin:$FB_HASH"

networks:
  vps:
    driver: bridge
YAML
( cd "$INSTALL_ROOT" && docker compose config >/dev/null )
ok "wrote $COMPOSE"

# ── Bring it up ────────────────────────────────────────────────────────────
say "starting stack"
( cd "$INSTALL_ROOT" && docker compose up -d )
sleep 3
( cd "$INSTALL_ROOT" && docker compose ps )

# ── Print next steps ───────────────────────────────────────────────────────
cat <<EOF

${C_GREEN}${C_BOLD}═══ Single-agent VPS installed ═══${C_RESET}

Domain          ${C_CYAN}https://$DOMAIN${C_RESET}
Files           ${C_CYAN}https://$DOMAIN/files/${C_RESET}  (admin / $FB_PASSWORD)
Bearer key      ${C_CYAN}$STATE_DIR/.bearer-key${C_RESET}
Install root    ${C_CYAN}$INSTALL_ROOT${C_RESET}
Provider key    ${C_CYAN}$SECRETS_FILE${C_RESET}  (${PROVIDER_KEY_ENV})

${C_BOLD}Use it from Hermes Studio${C_RESET}
  1. Open Studio → Choose Your Agent → Your VPS
  2. Paste:
       URL    https://$DOMAIN
       Key    $BEARER_KEY

${C_BOLD}Use it from Akela (A2A)${C_RESET}
       Card   https://$DOMAIN/.well-known/agent.json
       RPC    https://$DOMAIN/  (POST)
       Key    $BEARER_KEY

${C_BOLD}Smoke test (TLS may take 30-60s for the first cert)${C_RESET}
  ${C_DIM}curl -sS -H "Authorization: Bearer $BEARER_KEY" https://$DOMAIN/v1/health${C_RESET}

${C_BOLD}Truly nuke and reinstall${C_RESET}
  ${C_DIM}sudo rm -rf $STATE_DIR $INSTALL_ROOT${C_RESET}
  Don't do this casually — LE allows 5 certs per identifier set per 168h.

EOF
