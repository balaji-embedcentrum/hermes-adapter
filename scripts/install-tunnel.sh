#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# hermes-adapter — local agent + Cloudflare Quick Tunnel installer
#
# For Hermes Studio "Local (Tunnel)" mode. Runs the adapter on your own
# machine, exposes it through a free cloudflared Quick Tunnel, and prints
# the tunnel URL + bearer key in a copy-paste block for the Studio UI.
#
# The tunnel URL is EPHEMERAL — it rotates every time this script restarts.
# Keep the terminal open while you work; Ctrl-C to stop.
#
# Usage (macOS or Linux):
#   curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-tunnel.sh \
#     | bash
#
# Flags:
#   --model     OPTIONAL. Default: anthropic/claude-sonnet-4.6
#   --name      OPTIONAL. Agent name. Default: primary
#   --port      OPTIONAL. Local agent port. Default: 9001
#
# Requirements: Docker is NOT needed for this flow (install.sh uses a
# Python venv). You do need Python 3.10+, git, curl.
# ---------------------------------------------------------------------------

set -euo pipefail

# --- Colors / helpers -------------------------------------------------------

if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_BLUE=$'\033[34m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BLUE=""
fi

say()  { printf "%s[hermes-tunnel]%s %s\n" "$C_BLUE"   "$C_RESET" "$*"; }
ok()   { printf "%s✓%s %s\n"               "$C_GREEN"  "$C_RESET" "$*"; }
warn() { printf "%s⚠%s %s\n"               "$C_YELLOW" "$C_RESET" "$*"; }
die()  { printf "%s✗%s %s\n"               "$C_RED"    "$C_RESET" "$*" >&2; exit 1; }

# --- Args -------------------------------------------------------------------

MODEL="${MODEL:-anthropic/claude-sonnet-4.6}"
NAME="${NAME:-primary}"
PORT="${PORT:-9001}"

while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --name)  NAME="$2";  shift 2 ;;
    --port)  PORT="$2";  shift 2 ;;
    -h|--help)
      sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

HERMES_VENV="${HERMES_VENV:-$HOME/.hermes-venv}"
MANIFEST="$HOME/.hermes-adapter/agents.yaml"

# --- Step 1: install hermes-adapter (if not already) ------------------------

if [ ! -x "$HERMES_VENV/bin/hermes-adapter" ]; then
  say "Installing hermes-adapter into $HERMES_VENV"
  curl -fsSL \
    https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install.sh \
    | bash
else
  ok "hermes-adapter already installed at $HERMES_VENV"
fi

# Activate the venv for the rest of this script
# shellcheck disable=SC1091
source "$HERMES_VENV/bin/activate"

# --- Step 2: add the agent (if not already in manifest) ---------------------

if [ -f "$MANIFEST" ] && grep -q "^  $NAME:" "$MANIFEST"; then
  ok "agent '$NAME' already in $MANIFEST — keeping existing config"
else
  say "Adding agent '$NAME' with model $MODEL (you'll be prompted for the key)"
  hermes-adapter agent add "$NAME" \
    --model "$MODEL" \
    --port "$PORT" \
    --prompt-key
fi

# --- Step 3: install cloudflared --------------------------------------------

if ! command -v cloudflared >/dev/null 2>&1; then
  say "Installing cloudflared"
  case "$(uname -s)" in
    Darwin)
      if ! command -v brew >/dev/null 2>&1; then
        die "brew not found. Install from https://brew.sh or install cloudflared manually."
      fi
      brew install cloudflared
      ;;
    Linux)
      ARCH="$(uname -m)"
      case "$ARCH" in
        x86_64)  BIN="cloudflared-linux-amd64" ;;
        aarch64|arm64) BIN="cloudflared-linux-arm64" ;;
        *) die "unsupported arch: $ARCH" ;;
      esac
      SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo"
      $SUDO curl -fsSL \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/$BIN" \
        -o /usr/local/bin/cloudflared
      $SUDO chmod +x /usr/local/bin/cloudflared
      ;;
    *) die "unsupported OS: $(uname -s). Install cloudflared manually." ;;
  esac
  ok "cloudflared installed: $(cloudflared --version 2>&1 | head -1)"
else
  ok "cloudflared already installed: $(cloudflared --version 2>&1 | head -1)"
fi

# --- Step 4: start adapter (detached) ---------------------------------------

say "Starting adapter (detached)"
hermes-adapter up --detach

# Wait for adapter on $PORT
say "Waiting for adapter on http://127.0.0.1:$PORT"
for i in $(seq 1 30); do
  if curl -fsSL -o /dev/null "http://127.0.0.1:$PORT/health" 2>/dev/null; then
    ok "adapter healthy"
    break
  fi
  [ "$i" = "30" ] && die "adapter never became healthy on port $PORT"
  sleep 1
done

# --- Step 5: start cloudflared quick tunnel ---------------------------------

TUNNEL_LOG="$(mktemp -t hermes-cf-tunnel.XXXXXX)"
say "Starting Cloudflare Quick Tunnel → http://localhost:$PORT"
cloudflared tunnel --no-autoupdate --url "http://localhost:$PORT" \
  > "$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

cleanup() {
  echo ""
  say "Shutting down"
  kill "$TUNNEL_PID" 2>/dev/null || true
  hermes-adapter down 2>/dev/null || true
  rm -f "$TUNNEL_LOG"
}
trap cleanup EXIT INT TERM

# Wait for the trycloudflare.com URL to appear in logs
say "Waiting for tunnel URL (up to 30s)"
TUNNEL_URL=""
for i in $(seq 1 30); do
  if TUNNEL_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1)"; \
     [ -n "$TUNNEL_URL" ]; then
    break
  fi
  sleep 1
done

if [ -z "$TUNNEL_URL" ]; then
  warn "No tunnel URL seen in 30s. Last 20 lines of log:"
  tail -20 "$TUNNEL_LOG"
  die "cloudflared didn't publish a URL"
fi

# --- Step 6: read bearer key and print paste block --------------------------

BEARER_KEY="$(grep -E '^[[:space:]]+a2a_key:' "$MANIFEST" | head -1 | awk '{print $2}')"
[ -z "$BEARER_KEY" ] && die "could not read a2a_key from $MANIFEST"

cat <<EOF

${C_BOLD}${C_GREEN}═══════════════════════════════════════════════════════════════${C_RESET}
${C_BOLD}✓ Local agent tunneled via Cloudflare${C_RESET}

Paste these into Hermes Studio → Agents → Local (Tunnel):

  Name:      $NAME
  URL:       $TUNNEL_URL
  API Key:   $BEARER_KEY

${C_YELLOW}⚠  This URL rotates on restart. Keep this terminal open.${C_RESET}
${C_DIM}   Ctrl-C to stop tunnel + adapter.${C_RESET}
${C_BOLD}${C_GREEN}═══════════════════════════════════════════════════════════════${C_RESET}

EOF

# Stay foreground so Ctrl-C triggers the trap
wait "$TUNNEL_PID"
