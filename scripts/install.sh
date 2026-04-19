#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# hermes-adapter — one-shot installer
#
# Creates an isolated Python venv, installs stock hermes-agent + hermes-adapter
# into it, and bootstraps ~/.hermes-adapter with a fresh agents.yaml.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install.sh | bash
#   # or, from a cloned repo:
#   ./scripts/install.sh
#
# Flags (via environment variables):
#   HERMES_VENV             venv location           (default: ~/.hermes-venv)
#   HERMES_AGENT_REF        hermes-agent git ref    (default: main)
#   HERMES_ADAPTER_REF      hermes-adapter git ref  (default: main)
#   HERMES_SKIP_INIT=1      skip `hermes-adapter init`
#   HERMES_WORKSPACE_DIR    workspace root          (default: ~/hermes-workspaces)
#   HERMES_STUDIO_URL       CORS origin to allow    (default: https://hermes-studio.com)
# ---------------------------------------------------------------------------

set -euo pipefail

# --- Colors / helpers -------------------------------------------------------

if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_BLUE=$'\033[34m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BLUE=""
fi
say()  { printf "%s▸ %s%s\n" "$C_BOLD" "$1" "$C_RESET"; }
ok()   { printf "%s✓ %s%s\n" "$C_GREEN" "$1" "$C_RESET"; }
warn() { printf "%s⚠ %s%s\n" "$C_YELLOW" "$1" "$C_RESET"; }
die()  { printf "%s✗ %s%s\n" "$C_RED" "$1" "$C_RESET" >&2; exit 1; }

# --- Config -----------------------------------------------------------------

HERMES_VENV="${HERMES_VENV:-$HOME/.hermes-venv}"
# NOTE: upstream NousResearch/hermes-agent does not yet include the a2a_adapter
# package. Default to the fork/branch that does; override with env if needed.
HERMES_AGENT_REPO="${HERMES_AGENT_REPO:-balaji-embedcentrum/hermes-agent}"
HERMES_AGENT_REF="${HERMES_AGENT_REF:-feat/a2a-client-server-implementation}"
HERMES_ADAPTER_REF="${HERMES_ADAPTER_REF:-main}"
HERMES_WORKSPACE_DIR="${HERMES_WORKSPACE_DIR:-$HOME/hermes-workspaces}"
HERMES_STUDIO_URL="${HERMES_STUDIO_URL:-https://hermes-studio.com}"

# --- 1. Pick a Python interpreter ------------------------------------------

PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    ver="$($candidate -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    major="${ver%.*}"; minor="${ver#*.}"
    if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; then
      PY="$candidate"; break
    fi
  fi
done

if [ -z "$PY" ]; then
  die "Need Python 3.11+. Install one (e.g. 'brew install python@3.12' on macOS or 'apt install python3.12 python3.12-venv' on Debian/Ubuntu) and re-run."
fi
say "using Python: $($PY --version 2>&1) at $(command -v "$PY")"

# --- 2. Create venv ---------------------------------------------------------

if [ -d "$HERMES_VENV" ]; then
  warn "venv already exists at $HERMES_VENV — reusing"
else
  say "creating venv at $HERMES_VENV"
  "$PY" -m venv "$HERMES_VENV"
fi

# shellcheck disable=SC1091
source "$HERMES_VENV/bin/activate"
pip install --upgrade pip >/dev/null

# --- 3. Install hermes-agent + hermes-adapter -------------------------------

# hermes-agent from source. PEP 508 direct-URL form attaches extras correctly
# on modern pip. Default repo/ref points at the fork that carries a2a_adapter —
# upstream NousResearch/main doesn't ship the a2a console script yet.
say "installing ${HERMES_AGENT_REPO}@${HERMES_AGENT_REF}"
pip install "hermes-agent[a2a] @ git+https://github.com/${HERMES_AGENT_REPO}.git@${HERMES_AGENT_REF}"

# hermes-adapter: prefer a local editable install if this script lives in a clone
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(dirname -- "$SCRIPT_DIR")"
if [ -f "$REPO_ROOT/pyproject.toml" ] && grep -q "hermes-adapter" "$REPO_ROOT/pyproject.toml" 2>/dev/null; then
  say "installing hermes-adapter from local clone at $REPO_ROOT"
  pip install -e "${REPO_ROOT}[a2a]"
else
  say "installing hermes-adapter@${HERMES_ADAPTER_REF}"
  pip install "hermes-adapter[a2a] @ git+https://github.com/balaji-embedcentrum/hermes-adapter.git@${HERMES_ADAPTER_REF}"
fi

ok "packages installed"

# --- 4. Bootstrap ~/.hermes-adapter ----------------------------------------

if [ "${HERMES_SKIP_INIT:-0}" != "1" ]; then
  if [ -f "$HOME/.hermes-adapter/agents.yaml" ]; then
    warn "manifest already exists at ~/.hermes-adapter/agents.yaml — keeping it"
  else
    say "bootstrapping ~/.hermes-adapter"
    hermes-adapter init \
      --workspace-dir "$HERMES_WORKSPACE_DIR" \
      --cors-origins "$HERMES_STUDIO_URL"
  fi
fi

# --- 5. Print next steps ---------------------------------------------------

cat <<EOF

${C_BOLD}Installed.${C_RESET}

To use hermes-adapter in a new shell, activate the venv:
    source "$HERMES_VENV/bin/activate"

${C_BOLD}Add an agent${C_RESET} (repeat for each persona / model / provider):
    hermes-adapter agent add alpha \\
        --model anthropic/claude-sonnet-4.6 \\
        --prompt-key

    hermes-adapter agent add beta \\
        --model openrouter/meta-llama/llama-3.1-70b-instruct \\
        --prompt-key

${C_BOLD}Start the whole thing${C_RESET}:
    hermes-adapter up

${C_BOLD}Plug into Hermes Studio${C_RESET}: on $HERMES_STUDIO_URL, go to
Settings → My agents and paste:
    Adapter URL:  http://127.0.0.1:8766
    A2A bearer:   \$(grep a2a_key ~/.hermes-adapter/agents.yaml | awk '{print \$2}')
    Agents:       alpha=http://127.0.0.1:9001, beta=http://127.0.0.1:9002, ...

Logs:       ~/.hermes-adapter/logs/
Config:     ~/.hermes-adapter/agents.yaml
Stop:       hermes-adapter down

EOF
