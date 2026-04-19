#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# hermes-adapter — Docker installer (fresh VPS)
#
# Uses stock hermes-agent + hermes-adapter Docker images. The same
# agents.yaml that drives the venv-based local supervisor also drives the
# Docker Compose layout — switch runtimes without re-typing agent configs.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-docker.sh | bash
#   # or, from a cloned repo:
#   ./scripts/install-docker.sh
#
# Flags (via environment variables):
#   HERMES_ROOT              install root          (default: /srv/hermes-adapter)
#   HERMES_STUDIO_URL        CORS origin           (default: https://hermes-studio.com)
#   HERMES_WORKSPACE_DIR     workspace volume      (default: $HERMES_ROOT/workspaces)
#   HERMES_ADAPTER_IMAGE     image tag             (default: ghcr.io/balaji-embedcentrum/hermes-adapter:latest)
#   HERMES_AGENT_IMAGE       image tag             (default: noushermes/hermes-agent:latest)
#   HERMES_BIND              host bind address     (default: 127.0.0.1 — behind a reverse proxy)
# ---------------------------------------------------------------------------

set -euo pipefail

if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
else
  C_RESET=""; C_BOLD=""; C_GREEN=""; C_YELLOW=""; C_RED=""
fi
say()  { printf "%s▸ %s%s\n" "$C_BOLD" "$1" "$C_RESET"; }
ok()   { printf "%s✓ %s%s\n" "$C_GREEN" "$1" "$C_RESET"; }
warn() { printf "%s⚠ %s%s\n" "$C_YELLOW" "$1" "$C_RESET"; }
die()  { printf "%s✗ %s%s\n" "$C_RED" "$1" "$C_RESET" >&2; exit 1; }

# --- Config -----------------------------------------------------------------

HERMES_ROOT="${HERMES_ROOT:-/srv/hermes-adapter}"
HERMES_STUDIO_URL="${HERMES_STUDIO_URL:-https://hermes-studio.com}"
HERMES_WORKSPACE_DIR="${HERMES_WORKSPACE_DIR:-$HERMES_ROOT/workspaces}"
HERMES_ADAPTER_IMAGE="${HERMES_ADAPTER_IMAGE:-ghcr.io/balaji-embedcentrum/hermes-adapter:latest}"
HERMES_AGENT_IMAGE="${HERMES_AGENT_IMAGE:-noushermes/hermes-agent:latest}"
HERMES_BIND="${HERMES_BIND:-127.0.0.1}"

# --- 1. Check docker --------------------------------------------------------

if ! command -v docker >/dev/null 2>&1; then
  die "Docker not found. Install it first: https://docs.docker.com/engine/install/"
fi
if ! docker compose version >/dev/null 2>&1; then
  die "Docker Compose v2 not available. Install the docker-compose-plugin package."
fi
say "using Docker: $(docker --version)"

# --- 2. Create install root -------------------------------------------------

SUDO=""
if [ ! -w "$(dirname "$HERMES_ROOT")" ]; then
  SUDO="sudo"
fi
$SUDO mkdir -p "$HERMES_ROOT" "$HERMES_WORKSPACE_DIR"
$SUDO chown -R "$USER" "$HERMES_ROOT"

cd "$HERMES_ROOT"
ok "install root: $HERMES_ROOT"

# --- 3. Pull images ---------------------------------------------------------

say "pulling $HERMES_ADAPTER_IMAGE"
if ! docker pull "$HERMES_ADAPTER_IMAGE" 2>/dev/null; then
  warn "image not published yet — falling back to a local build"
  # If run from a clone of the repo, build in place
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
  REPO_ROOT="$(dirname -- "$SCRIPT_DIR")"
  if [ -f "$REPO_ROOT/Dockerfile" ]; then
    ( cd "$REPO_ROOT" && docker build -t "$HERMES_ADAPTER_IMAGE" . )
  else
    die "No Dockerfile nearby. Clone https://github.com/balaji-embedcentrum/hermes-adapter first, or wait for the image to be published."
  fi
fi
say "pulling $HERMES_AGENT_IMAGE"
docker pull "$HERMES_AGENT_IMAGE" || warn "could not pull $HERMES_AGENT_IMAGE — check the image name"

# --- 4. Bootstrap agents.yaml via the adapter image ------------------------

if [ -f "$HERMES_ROOT/agents.yaml" ]; then
  warn "agents.yaml already exists at $HERMES_ROOT/agents.yaml — keeping it"
else
  say "scaffolding $HERMES_ROOT/agents.yaml"
  docker run --rm \
    -v "$HERMES_ROOT:/root/.hermes-adapter" \
    -e HERMES_ADAPTER_HOME=/root/.hermes-adapter \
    "$HERMES_ADAPTER_IMAGE" \
    hermes-adapter init \
      --workspace-dir "$HERMES_WORKSPACE_DIR" \
      --cors-origins "$HERMES_STUDIO_URL"
fi

# --- 5. Convenience wrapper: hermesctl --------------------------------------

WRAPPER="$HERMES_ROOT/hermesctl"
cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
# Thin wrapper: runs hermes-adapter CLI inside a throwaway container with
# the install root mounted so every command sees the shared agents.yaml.
set -euo pipefail
exec docker run --rm -it \\
  -v "$HERMES_ROOT:/root/.hermes-adapter" \\
  -e HERMES_ADAPTER_HOME=/root/.hermes-adapter \\
  "$HERMES_ADAPTER_IMAGE" \\
  hermes-adapter "\$@"
EOF
chmod +x "$WRAPPER"
ok "wrapper script: $WRAPPER  (use './hermesctl agent add ...', etc.)"

# --- 6. Print next steps ----------------------------------------------------

cat <<EOF

${C_BOLD}Installed.${C_RESET}

${C_BOLD}1. Add agents${C_RESET} (repeat per model / provider; keys are stored under $HERMES_ROOT/agents/<name>/.env):

    cd $HERMES_ROOT
    ./hermesctl agent add alpha --model anthropic/claude-sonnet-4.6 --key sk-ant-...
    ./hermesctl agent add beta  --model openai/gpt-5 --key sk-...

${C_BOLD}2. Generate docker-compose.yml${C_RESET} from your agents.yaml:

    ./hermesctl compose generate --bind $HERMES_BIND \\
      --image $HERMES_ADAPTER_IMAGE \\
      --hermes-agent-image $HERMES_AGENT_IMAGE \\
      -o docker-compose.yml

${C_BOLD}3. Bring the stack up${C_RESET}:

    docker compose up -d
    docker compose ps

${C_BOLD}4. Front with TLS${C_RESET} (Caddy example; put in front of docker-compose.yml):

    alice.example.com {
        handle_path /ws/*         { reverse_proxy 127.0.0.1:8766 }
        handle     /ws            { reverse_proxy 127.0.0.1:8766 }
        handle_path /a2a/alpha/*  { reverse_proxy 127.0.0.1:9001 }
        handle_path /a2a/beta/*   { reverse_proxy 127.0.0.1:9002 }
    }

Config:  $HERMES_ROOT/agents.yaml
Logs:    docker compose logs -f
Stop:    docker compose down

EOF
