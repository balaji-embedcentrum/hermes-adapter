#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# hermes-adapter — single-agent VPS installer for Hermes Studio "Your VPS"
#
# Thin wrapper around install-fleet.sh that provisions one OpenAI-compat
# agent with TLS and prints the URL + bearer key in a copy-paste block for
# the Hermes Studio /agents → Your VPS form.
#
# Usage (run as root on a fresh Ubuntu 22/24 VPS):
#   curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-studio-vps.sh \
#     | bash -s -- --domain agent.example.com --email you@example.com
#
# Flags:
#   --domain       REQUIRED. Public hostname. DNS A record must already
#                  point at this host for Let's Encrypt HTTP-01 to succeed.
#   --email        REQUIRED. Email for Let's Encrypt renewal notices.
#   --model        OPTIONAL. Default: anthropic/claude-sonnet-4.6.
#   --name         OPTIONAL. Agent name. Default: primary.
#   --model-key    OPTIONAL. Model provider API key. If omitted you'll be
#                  prompted interactively by `./fleet set`.
#   --studio-url   OPTIONAL. CORS origin. Default: https://hermes-studio.com.
#
# Env overrides:
#   FLEET_ROOT     Install root (default: /srv/hermes-fleet) — passed
#                  through to install-fleet.sh.
# ---------------------------------------------------------------------------

set -euo pipefail

# --- Colors / helpers -------------------------------------------------------

if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_BLUE=$'\033[34m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BLUE=""
fi

say()  { printf "%s[hermes-studio-vps]%s %s\n"  "$C_BLUE"   "$C_RESET" "$*"; }
ok()   { printf "%s✓%s %s\n"                    "$C_GREEN"  "$C_RESET" "$*"; }
warn() { printf "%s⚠%s %s\n"                    "$C_YELLOW" "$C_RESET" "$*"; }
die()  { printf "%s✗%s %s\n"                    "$C_RED"    "$C_RESET" "$*" >&2; exit 1; }

# --- Args -------------------------------------------------------------------

DOMAIN=""
EMAIL=""
MODEL="${MODEL:-anthropic/claude-sonnet-4.6}"
NAME="${NAME:-primary}"
MODEL_KEY=""
STUDIO_URL="${STUDIO_URL:-https://hermes-studio.com}"

while [ $# -gt 0 ]; do
  case "$1" in
    --domain)     DOMAIN="$2";     shift 2 ;;
    --email)      EMAIL="$2";      shift 2 ;;
    --model)      MODEL="$2";      shift 2 ;;
    --name)       NAME="$2";       shift 2 ;;
    --model-key)  MODEL_KEY="$2";  shift 2 ;;
    --studio-url) STUDIO_URL="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

[ -z "$DOMAIN" ] && die "--domain is required (e.g. --domain agent.example.com)"
[ -z "$EMAIL" ] && die "--email is required (for Let's Encrypt)"

FLEET_ROOT="${FLEET_ROOT:-/srv/hermes-fleet}"

# --- Install the fleet ------------------------------------------------------

say "Bootstrapping single-agent fleet at $FLEET_ROOT (domain: $DOMAIN)"

# Delegate to install-fleet.sh with --names=<NAME> for a single agent.
# Pass all relevant flags; install-fleet is idempotent so re-runs are safe.
FLEET_ROOT="$FLEET_ROOT" curl -fsSL \
  https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-fleet.sh \
  | bash -s -- \
      --domain "$DOMAIN" \
      --acme-email "$EMAIL" \
      --protocol openai \
      --names "$NAME" \
      --studio-url "$STUDIO_URL"

cd "$FLEET_ROOT"

# --- Configure the agent's model + key --------------------------------------

if [ -n "$MODEL_KEY" ]; then
  ./fleet set "$NAME" --model "$MODEL" --key "$MODEL_KEY"
else
  say "Setting model for $NAME — you'll be prompted for the provider API key"
  ./fleet set "$NAME" --model "$MODEL"
fi

# --- Start ------------------------------------------------------------------

say "Starting agent containers (first run may take 2-3 minutes for TLS cert)"
./fleet up

# --- Read the bearer key ----------------------------------------------------

if [ ! -f "$FLEET_ROOT/.bearer-key" ]; then
  die "bearer key not found at $FLEET_ROOT/.bearer-key — did install-fleet.sh complete?"
fi
BEARER_KEY="$(cat "$FLEET_ROOT/.bearer-key")"

# --- Paste block ------------------------------------------------------------

cat <<EOF

${C_BOLD}${C_GREEN}═══════════════════════════════════════════════════════════════${C_RESET}
${C_BOLD}✓ Your VPS agent is running${C_RESET}

Paste these into Hermes Studio → Agents → Your VPS:

  Name:      $NAME
  URL:       https://$DOMAIN
  API Key:   $BEARER_KEY

${C_DIM}Logs:      cd $FLEET_ROOT && docker compose logs -f${C_RESET}
${C_DIM}Stop:      cd $FLEET_ROOT && ./fleet down${C_RESET}
${C_BOLD}${C_GREEN}═══════════════════════════════════════════════════════════════${C_RESET}

EOF
