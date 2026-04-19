#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# hermes-adapter — add-adapter-only installer (hermes already installed)
#
# Detects how hermes-agent is already running on this host and plugs the
# adapter in alongside it:
#
#   1. venv mode          an existing Python venv already has hermes-agent
#                          → pip install hermes-adapter into the SAME venv
#   2. docker mode         a `noushermes/hermes-agent` container is running,
#                          or you pass HERMES_MODE=docker manually
#                          → pull/build the adapter image, write a sidecar
#                            docker-compose.override.yml, run `compose up -d`
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-adapter.sh | bash
#
# Flags:
#   HERMES_MODE=venv|docker      force detection result
#   HERMES_VENV=/path/to/venv    override detected venv location
#   HERMES_ROOT=/srv/hermes      docker compose directory (default: cwd if it has docker-compose.yml, else /srv/hermes-adapter)
#   HERMES_ADAPTER_IMAGE         image tag (default: ghcr.io/balaji-embedcentrum/hermes-adapter:latest)
#   HERMES_ADAPTER_REF           git ref for source install (default: main)
#   HERMES_SKIP_INIT=1           don't scaffold agents.yaml
#   HERMES_STUDIO_URL            CORS origin (default: https://hermes-studio.com)
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

HERMES_MODE="${HERMES_MODE:-}"
HERMES_STUDIO_URL="${HERMES_STUDIO_URL:-https://hermes-studio.com}"
HERMES_ADAPTER_REF="${HERMES_ADAPTER_REF:-main}"
HERMES_ADAPTER_IMAGE="${HERMES_ADAPTER_IMAGE:-ghcr.io/balaji-embedcentrum/hermes-adapter:latest}"

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

detect_mode() {
  if [ -n "${HERMES_MODE:-}" ]; then
    echo "$HERMES_MODE"
    return
  fi

  # Prefer Docker if a hermes-agent container is running
  if command -v docker >/dev/null 2>&1; then
    if docker ps --format '{{.Image}}' 2>/dev/null | grep -q "hermes-agent"; then
      echo "docker"
      return
    fi
  fi

  # Otherwise, look for an existing venv with hermes-agent
  for candidate in \
      "${HERMES_VENV:-}" \
      "$HOME/.hermes-venv" \
      "$HOME/.venv-hermes" \
      "$(command -v hermes 2>/dev/null | xargs -I{} dirname {} 2>/dev/null | xargs -I{} dirname {} 2>/dev/null)"; do
    [ -z "$candidate" ] && continue
    if [ -x "$candidate/bin/python" ] && "$candidate/bin/python" -c "import run_agent" 2>/dev/null; then
      echo "venv:$candidate"
      return
    fi
  done

  die "Could not detect an existing hermes install. Set HERMES_MODE=venv HERMES_VENV=/path manually, or use install.sh for a fresh install."
}

MODE="$(detect_mode)"
say "detected: $MODE"

# ---------------------------------------------------------------------------
# VENV path — pip install into existing venv
# ---------------------------------------------------------------------------

install_venv() {
  local venv="$1"
  say "installing hermes-adapter into $venv"

  # shellcheck disable=SC1091
  source "$venv/bin/activate"

  # Prefer editable install from a nearby clone, else install from git
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
  REPO_ROOT="$(dirname -- "$SCRIPT_DIR")"
  if [ -f "$REPO_ROOT/pyproject.toml" ] && grep -q "hermes-adapter" "$REPO_ROOT/pyproject.toml" 2>/dev/null; then
    pip install -e "${REPO_ROOT}[a2a]"
  else
    pip install "hermes-adapter[a2a] @ git+https://github.com/balaji-embedcentrum/hermes-adapter.git@${HERMES_ADAPTER_REF}"
  fi

  ok "adapter installed"

  if [ "${HERMES_SKIP_INIT:-0}" != "1" ]; then
    if [ -f "$HOME/.hermes-adapter/agents.yaml" ]; then
      warn "~/.hermes-adapter/agents.yaml already exists — keeping it"
    else
      hermes-adapter init --cors-origins "$HERMES_STUDIO_URL"
    fi
  fi

  cat <<EOF

${C_BOLD}Installed into venv:${C_RESET} $venv

Activate it in new shells with:
    source "$venv/bin/activate"

Add an agent:
    hermes-adapter agent add alpha --model anthropic/claude-sonnet-4.6 --prompt-key

Start the supervisor (workspace API + every agent):
    hermes-adapter up

EOF
}

# ---------------------------------------------------------------------------
# DOCKER path — sidecar compose override
# ---------------------------------------------------------------------------

install_docker() {
  if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    die "Docker or Compose v2 missing. Install them first."
  fi

  # Pick install dir: prefer one with a docker-compose.yml, otherwise /srv/hermes-adapter
  if [ -z "${HERMES_ROOT:-}" ]; then
    if [ -f "./docker-compose.yml" ]; then
      HERMES_ROOT="$PWD"
    else
      HERMES_ROOT="/srv/hermes-adapter"
    fi
  fi
  SUDO=""
  [ ! -w "$(dirname "$HERMES_ROOT")" ] && SUDO="sudo"
  $SUDO mkdir -p "$HERMES_ROOT"
  $SUDO chown -R "$USER" "$HERMES_ROOT"
  cd "$HERMES_ROOT"

  say "install root: $HERMES_ROOT"

  # Pull or build the adapter image
  if ! docker pull "$HERMES_ADAPTER_IMAGE" 2>/dev/null; then
    warn "image not published yet — building locally from the repo clone"
    SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
    REPO_ROOT="$(dirname -- "$SCRIPT_DIR")"
    [ -f "$REPO_ROOT/Dockerfile" ] || die "No Dockerfile nearby. Clone the repo first."
    ( cd "$REPO_ROOT" && docker build -t "$HERMES_ADAPTER_IMAGE" . )
  fi

  # Scaffold agents.yaml if absent
  if [ ! -f "$HERMES_ROOT/agents.yaml" ] && [ "${HERMES_SKIP_INIT:-0}" != "1" ]; then
    docker run --rm \
      -v "$HERMES_ROOT:/root/.hermes-adapter" \
      -e HERMES_ADAPTER_HOME=/root/.hermes-adapter \
      "$HERMES_ADAPTER_IMAGE" \
      hermes-adapter init --cors-origins "$HERMES_STUDIO_URL"
  fi

  # Write a docker-compose.override.yml that just adds the adapter service
  cat > "$HERMES_ROOT/docker-compose.override.yml" <<EOF
services:
  adapter:
    image: $HERMES_ADAPTER_IMAGE
    container_name: hermes-adapter
    restart: unless-stopped
    command: ["hermes-adapter", "workspace"]
    environment:
      HERMES_ADAPTER_HOST: 0.0.0.0
      HERMES_ADAPTER_PORT: 8766
      HERMES_WORKSPACE_DIR: /workspaces
      HERMES_ADAPTER_CORS_ORIGINS: $HERMES_STUDIO_URL
    volumes:
      - \${HERMES_WORKSPACE_DIR:-./workspaces}:/workspaces
      - $HERMES_ROOT:/root/.hermes-adapter
    ports:
      - "127.0.0.1:8766:8766"
EOF

  # Convenience wrapper
  cat > "$HERMES_ROOT/hermesctl" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec docker run --rm -it \\
  -v "$HERMES_ROOT:/root/.hermes-adapter" \\
  -e HERMES_ADAPTER_HOME=/root/.hermes-adapter \\
  "$HERMES_ADAPTER_IMAGE" \\
  hermes-adapter "\$@"
EOF
  chmod +x "$HERMES_ROOT/hermesctl"

  cat <<EOF

${C_BOLD}Adapter added as a sidecar.${C_RESET}

Files written to $HERMES_ROOT:
  - docker-compose.override.yml   (adapter service, merged with your existing compose)
  - agents.yaml                   (shared config)
  - hermesctl                     (wrapper for \`hermes-adapter\` commands inside Docker)

Add agents:
    cd $HERMES_ROOT
    ./hermesctl agent add alpha --model anthropic/claude-sonnet-4.6 --key sk-ant-...

Bring the adapter up alongside your existing hermes:
    docker compose up -d adapter

EOF
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

case "$MODE" in
  venv) install_venv "${HERMES_VENV:?HERMES_VENV must point at the existing hermes venv}" ;;
  venv:*) install_venv "${MODE#venv:}" ;;
  docker) install_docker ;;
  *) die "Unknown mode: $MODE" ;;
esac
