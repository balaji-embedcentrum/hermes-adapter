#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# hermes-adapter — agent fleet installer (one-shot)
#
# Every agent in the fleet runs the unified gateway
# (hermes_adapter.gateway) which exposes three protocols on one port:
#   /v1/chat/completions, /v1/models      OpenAI-compatible  (Studio, Akela fallback)
#   /.well-known/agent.json, POST /        A2A JSON-RPC       (Akela, Vertex, LangGraph)
#   /ws/*                                 Workspace file+git (Studio IDE)
#
# The fleet brings up:
#   - 1× Traefik    (TLS via Let's Encrypt)
#   - 1× adapter    (/fleet/claim control plane + shared /ws/* for claim flow)
#   - N× agents     (one per persona, each running the unified gateway)
#
# After install, you fill in each agent's model + key with the generated
# `./fleet` helper, then `./fleet up` starts the agent containers.
#
# Usage (fresh install):
#   curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-fleet.sh \
#     | bash -s -- \
#         --domain agents.example.com \
#         --acme-email you@example.com
#
# Usage (migrate an existing fleet to the per-session bind-mount
# architecture — idempotent, safe to re-run):
#   curl -fsSL .../install-fleet.sh | bash -s -- --upgrade
#   # or, if already checked out:
#   FLEET_ROOT=/srv/hermes-fleet ./scripts/install-fleet.sh --upgrade
#
# Flags:
#   --upgrade        Migrate an existing install at $FLEET_ROOT to the
#                    /fleet/claim per-session bind-mount architecture.
#                    Patches docker-compose.yml + Caddyfile (if present),
#                    creates sentinel workspace + override dir, rebuilds
#                    adapter image, recreates containers. No other flags
#                    needed. Exits after migration.
#   --domain         REQUIRED for fresh install. Public hostname of the
#                    agent VPS (DNS A record must already point here).
#   --acme-email     REQUIRED for fresh install. Let's Encrypt renewal
#                    contact email.
#   --protocol       DEPRECATED. Accepted for backward compatibility but
#                    ignored — every agent now serves OpenAI + A2A + workspace
#                    simultaneously.
#   --studio-url     OPTIONAL. CORS origin for Studio.
#   --names          OPTIONAL. Space-separated agent names. Mutually
#                    exclusive with --personas-file.
#                    (default: "emma mateo aarav mei lea sofia yuki priya lukas diego")
#   --personas-file  OPTIONAL. Path to a JSON array of richer persona
#                    objects: [{name, display, role, skills[], personality},
#                    ...]. When present, populates AGENT_NAME /
#                    AGENT_DESCRIPTION / AGENT_SKILLS env so each agent's
#                    /.well-known/agent.json + /v1/models card render correctly.
#                    Also seeds agents/<name>/persona.md
#                    with the personality blurb for you to flesh out.
#
# Env overrides:
#   FLEET_ROOT       install root           (default: /srv/hermes-fleet)
#   ADAPTER_IMAGE    hermes-adapter image   (used for both fleet control
#                    plane AND per-agent containers)
#   TRAEFIK_IMAGE    traefik image
# ---------------------------------------------------------------------------

set -euo pipefail

# --- Color helpers ----------------------------------------------------------
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_CYAN=$'\033[36m'
else
  C_RESET=""; C_BOLD=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_CYAN=""
fi
say()  { printf "%s▸ %s%s\n" "$C_BOLD" "$1" "$C_RESET"; }
ok()   { printf "%s✓ %s%s\n" "$C_GREEN" "$1" "$C_RESET"; }
warn() { printf "%s⚠ %s%s\n" "$C_YELLOW" "$1" "$C_RESET"; }
die()  { printf "%s✗ %s%s\n" "$C_RED" "$1" "$C_RESET" >&2; exit 1; }

# --- Self-bootstrap (curl|bash friendly) -----------------------------------
#
# This script is designed to work as a single-command install on a naked
# Ubuntu 22/24 VPS. When piped through bash:
#
#   curl -fsSL .../install-fleet.sh | bash -s -- --domain X --acme-email Y
#
# we won't have the rest of the hermes-adapter repo on disk, so we:
#   1. install Docker + compose plugin (via get.docker.com) if missing
#   2. install git if missing (needed to clone the repo)
#   3. clone hermes-adapter to $BOOTSTRAP_DIR (default /opt/hermes-adapter)
#   4. re-exec this script from the cloned tree so the rest of the flow
#      can docker-build the adapter image locally and read sibling files
#
# When you've already cloned the repo manually and are running
# ./scripts/install-fleet.sh, all of this is skipped.
#
BOOTSTRAP_DIR="${BOOTSTRAP_DIR:-/opt/hermes-adapter}"
BOOTSTRAP_REPO="${BOOTSTRAP_REPO:-https://github.com/balaji-embedcentrum/hermes-adapter.git}"
BOOTSTRAP_REF="${BOOTSTRAP_REF:-main}"
SUDO=""; [ "$(id -u)" = "0" ] || SUDO="sudo"

_ensure_tool() {
  # Install one of the listed packages if the binary is missing.
  local bin="$1"; shift
  if ! command -v "$bin" >/dev/null 2>&1; then
    say "installing $bin"
    $SUDO apt-get update -qq
    $SUDO apt-get install -y --no-install-recommends "$@"
  fi
}

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

# Are we running from a clone of the hermes-adapter repo? If yes, no
# bootstrap needed — sibling files (Dockerfile, pyproject.toml, module
# source) are on disk and we can docker-build locally. If no, we're
# running via curl|bash or similar and need to clone + re-exec.
_need_bootstrap() {
  local src="${BASH_SOURCE[0]:-}"
  [ -n "$src" ] && [ -f "$src" ] || return 0
  local dir
  dir="$(cd -- "$(dirname -- "$src")/.." &>/dev/null && pwd || echo "")"
  [ -n "$dir" ] && [ -f "$dir/Dockerfile" ] && [ -f "$dir/pyproject.toml" ] || return 0
  return 1
}

if _need_bootstrap; then
  _ensure_tool git git ca-certificates
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
  ok "bootstrap complete — re-exec'ing from $BOOTSTRAP_DIR/scripts/install-fleet.sh"
  exec "$BOOTSTRAP_DIR/scripts/install-fleet.sh" "$@"
fi

# Docker must be available from this point on.
_ensure_docker
# End self-bootstrap -----------------------------------------------------

# --- Parse flags ------------------------------------------------------------
DOMAIN=""
ACME_EMAIL=""
STUDIO_URL=""
PROTOCOL="openai"
PERSONAS_FILE=""
AGENT_NAMES_DEFAULT="emma mateo aarav mei lea sofia yuki priya lukas diego"
AGENT_NAMES=""
UPGRADE_MODE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --upgrade)        UPGRADE_MODE=1;      shift ;;
    --domain)         DOMAIN="$2";         shift 2 ;;
    --acme-email)     ACME_EMAIL="$2";     shift 2 ;;
    --studio-url)     STUDIO_URL="$2";     shift 2 ;;
    --protocol)       PROTOCOL="$2";       shift 2 ;;
    --personas-file)  PERSONAS_FILE="$2";  shift 2 ;;
    --names)          AGENT_NAMES="$2";    shift 2 ;;
    -h|--help)
      # Print the header doc block between the two "# ---" dividers.
      awk 'BEGIN{n=0} /^# ---/ {n++; if(n==2) exit; next} n==1 {sub(/^# ?/,""); print}' "$0"
      exit 0 ;;
    *) die "unknown flag: $1 (try --help)" ;;
  esac
done

# --- Upgrade mode -----------------------------------------------------------
# Re-run this script with --upgrade to migrate an existing fleet to the
# /fleet/claim per-session bind-mount architecture without re-provisioning.
# Idempotent; safe to run multiple times. Exits after migration.
if [ "$UPGRADE_MODE" = "1" ]; then
  FLEET_ROOT="${FLEET_ROOT:-/srv/hermes-fleet}"

  say "upgrade mode — migrating existing fleet at $FLEET_ROOT"
  [ -f "$FLEET_ROOT/docker-compose.yml" ] || die "no docker-compose.yml at $FLEET_ROOT — nothing to upgrade"
  [ -f "$FLEET_ROOT/.bearer-key" ]        || die "no .bearer-key at $FLEET_ROOT — not an install-fleet deployment"
  command -v docker >/dev/null            || die "docker CLI not available"
  command -v python3 >/dev/null           || die "python3 is required (for YAML patching). apt-get install -y python3 python3-yaml"
  python3 -c "import yaml" 2>/dev/null    || die "PyYAML missing. Install: apt-get install -y python3-yaml (or: pip3 install pyyaml)"

  cd "$FLEET_ROOT"

  # 1. Backup docker-compose.yml
  if [ ! -f docker-compose.yml.pre-fleet-bak ]; then
    cp docker-compose.yml docker-compose.yml.pre-fleet-bak
    ok "backed up → $FLEET_ROOT/docker-compose.yml.pre-fleet-bak"
  else
    warn "backup already exists at $FLEET_ROOT/docker-compose.yml.pre-fleet-bak — keeping first one"
  fi

  # 2. Sentinel workspace + override dir
  mkdir -p workspaces/_unclaimed compose.override
  chmod 755 workspaces/_unclaimed
  ok "sentinel workspace + compose.override/ ready"

  # 3. Patch docker-compose.yml via PyYAML
  BEARER_KEY="$(cat .bearer-key)"
  export FLEET_ROOT BEARER_KEY
  python3 <<'PY'
import os, re, sys, yaml

fleet_root = os.environ["FLEET_ROOT"]
bearer = os.environ["BEARER_KEY"]
path = "docker-compose.yml"

with open(path) as f:
    compose = yaml.safe_load(f)

services = compose.setdefault("services", {})

# --- Adapter service patch -------------------------------------------------
# Support both `adapter` and `hermes-fleet-adapter` service names.
adapter_svc = None
for candidate in ("adapter", "hermes-fleet-adapter"):
    if candidate in services:
        adapter_svc = candidate
        break
if adapter_svc is None:
    print("!! no adapter service found in docker-compose.yml", file=sys.stderr)
    sys.exit(2)

svc = services[adapter_svc]

# Normalise environment to a dict (compose allows list or dict form).
env = svc.get("environment", {})
if isinstance(env, list):
    env_dict = {}
    for item in env:
        if not isinstance(item, str):
            continue
        if "=" in item:
            k, v = item.split("=", 1)
        elif ":" in item:
            k, v = item.split(":", 1)
        else:
            continue
        env_dict[k.strip()] = v.strip()
    env = env_dict
svc["environment"] = env

env["HERMES_FLEET_MODE"] = "1"
env["FLEET_ROOT"] = "/srv/hermes-fleet"
env.setdefault("FLEET_CONTROL_KEY", "${BEARER_KEY}")

# Mounts — docker socket + fleet root, idempotent by destination.
volumes = svc.setdefault("volumes", [])
def has_target(vlist, target):
    for v in vlist:
        if isinstance(v, str) and (v.endswith(":" + target) or v.endswith(":" + target + ":ro")):
            return True
        if isinstance(v, dict) and v.get("target") == target:
            return True
    return False

if not has_target(volumes, "/srv/hermes-fleet"):
    volumes.append(f"{fleet_root}:/srv/hermes-fleet")
if not has_target(volumes, "/var/run/docker.sock"):
    volumes.append("/var/run/docker.sock:/var/run/docker.sock")

print(f"  adapter: env + mounts patched (service={adapter_svc})")

# --- Traefik label for /fleet/* -------------------------------------------
# Only patched when the adapter is fronted by Traefik (labels present).
# Caddy deployments are patched separately below.
labels = svc.get("labels", [])
if isinstance(labels, list) and any("traefik" in (s or "") for s in labels):
    if not any("routers.fleet" in (s or "") for s in labels):
        labels.extend([
            "traefik.http.routers.fleet.rule=Host(`${DOMAIN}`) && PathPrefix(`/fleet`)",
            "traefik.http.routers.fleet.entrypoints=websecure",
            "traefik.http.routers.fleet.tls.certresolver=le",
            "traefik.http.services.fleet.loadbalancer.server.port=8766",
        ])
        svc["labels"] = labels
        print("  adapter: Traefik /fleet/* route added")
    else:
        print("  adapter: Traefik /fleet/* route already present")

# --- Agent services: swap shared workspace for sentinel --------------------
SHARED_PATTERNS = (
    re.compile(r"^\./workspaces(:|$)"),          # ./workspaces[:anything]
    re.compile(r"^\./workspaces/[^/_]"),         # ./workspaces/<existing-user>:
)
SENTINEL_MOUNT = "./workspaces/_unclaimed:/opt/workspaces:ro"
LEGACY_SHARED_TARGETS = ("/workspaces", "/opt/workspaces")

patched = 0
for name, s in services.items():
    if not name.startswith("hermes-agent-"):
        continue
    vols = s.get("volumes", [])
    new_vols = []
    swapped = False
    for v in vols:
        if not isinstance(v, str):
            new_vols.append(v)
            continue
        # Strip any default shared-workspace mounts; we'll replace with sentinel.
        if any(v.endswith(":" + t) or v.endswith(":" + t + ":ro") for t in LEGACY_SHARED_TARGETS):
            # Only strip when the source looks like the shared dir
            if v.startswith("./workspaces:") or v.startswith("./workspaces/_"):
                swapped = True
                continue
        new_vols.append(v)
    if SENTINEL_MOUNT not in new_vols:
        new_vols.insert(0, SENTINEL_MOUNT)
        swapped = True
    s["volumes"] = new_vols

    # Ensure HERMES_WORKSPACE_DIR is set so repo_finder scopes to the mount.
    ae = s.get("environment", {})
    if isinstance(ae, dict):
        if ae.get("HERMES_WORKSPACE_DIR") != "/opt/workspaces":
            ae["HERMES_WORKSPACE_DIR"] = "/opt/workspaces"
            s["environment"] = ae
            swapped = True
    if swapped:
        patched += 1

print(f"  agents: {patched} service(s) switched to sentinel mount")

with open(path, "w") as f:
    yaml.safe_dump(compose, f, default_flow_style=False, sort_keys=False)
print("docker-compose.yml rewritten")
PY
  ok "docker-compose.yml patched"

  # 4. Caddy patch — only runs if Caddyfile is present.
  if [ -f Caddyfile ]; then
    if grep -q "PathRegexp\|path /fleet\|@fleet" Caddyfile 2>/dev/null && grep -q "fleet" Caddyfile; then
      ok "Caddyfile already has /fleet route"
    else
      say "patching Caddyfile with /fleet/* route"
      python3 <<'PY'
import re
with open("Caddyfile") as f:
    content = f.read()
fleet_block = """\
    # Fleet control plane — claim/unclaim/status. Bearer-auth'd in the adapter.
    @fleet path /fleet*
    handle @fleet {
        reverse_proxy adapter:8766
    }

"""
# Insert right after the opening brace of the first domain site block.
pattern = re.compile(r'^([a-z0-9][a-z0-9.-]+\.[a-z]{2,}\s*\{\n)', re.M)
new, n = pattern.subn(r'\1' + fleet_block, content, count=1)
if n > 0:
    with open("Caddyfile", "w") as f:
        f.write(new)
    print("Caddyfile patched")
else:
    print("!! could not find a site block in Caddyfile — skipping")
PY
      ok "Caddyfile patched"
    fi
  else
    warn "no Caddyfile at $FLEET_ROOT/Caddyfile — skipping Caddy patch (Traefik labels were updated if applicable)"
  fi

  # 5. Pull + rebuild adapter image so it has docker CLI baked in.
  say "pulling latest adapter image"
  docker pull "${ADAPTER_IMAGE:-ghcr.io/balaji-embedcentrum/hermes-adapter:latest}" || \
    warn "pull failed — will use local image if present"

  # 6. Recreate containers with new env + mounts.
  say "recreating containers with new config"
  if [ -x ./fleet ]; then
    ./fleet reload 2>&1 | tail -20 || warn "./fleet reload exited non-zero"
  else
    docker compose up -d --force-recreate
  fi

  cat <<EOF

${C_GREEN}${C_BOLD}═══ Upgrade complete ═══${C_RESET}

Verify the adapter picked up fleet mode:
  ${C_DIM}docker exec hermes-fleet-adapter printenv | grep -E 'FLEET|HERMES_FLEET'${C_RESET}

Verify sentinel exists:
  ${C_DIM}ls -la $FLEET_ROOT/workspaces/_unclaimed${C_RESET}

Test claim (needs Studio PR merged + bearer auth):
  ${C_DIM}curl -fsS -X POST -H "Authorization: Bearer \$(cat $FLEET_ROOT/.bearer-key)" \\
    -H 'Content-Type: application/json' \\
    -d '{"agent":"isabelle","user":"balaji-embedcentrum"}' \\
    https://${DOMAIN:-<DOMAIN>}/fleet/claim${C_RESET}

Rollback (if needed):
  ${C_DIM}cp $FLEET_ROOT/docker-compose.yml.pre-fleet-bak $FLEET_ROOT/docker-compose.yml${C_RESET}
  ${C_DIM}./fleet reload${C_RESET}

EOF
  exit 0
fi
# --- End upgrade mode -------------------------------------------------------

[ -n "$DOMAIN" ]     || die "missing required --domain"
[ -n "$ACME_EMAIL" ] || die "missing required --acme-email"
[ -z "$PERSONAS_FILE" ] || [ -z "$AGENT_NAMES" ] \
  || die "--personas-file and --names are mutually exclusive"
[ -z "$PERSONAS_FILE" ] || [ -f "$PERSONAS_FILE" ] \
  || die "--personas-file not found: $PERSONAS_FILE"
[ "$PROTOCOL" = "openai" ] || [ "$PROTOCOL" = "a2a" ] || \
  warn "--protocol is deprecated and ignored; every agent now serves both"

# When --personas-file is given, jq is required to parse it
if [ -n "$PERSONAS_FILE" ]; then
  command -v jq >/dev/null \
    || die "jq is required when using --personas-file. Install: sudo apt-get install -y jq"
  AGENT_NAMES="$(jq -r '.[].name' "$PERSONAS_FILE" | tr '\n' ' ')"
  [ -n "$AGENT_NAMES" ] || die "no agents found in $PERSONAS_FILE"
fi
[ -n "$AGENT_NAMES" ] || AGENT_NAMES="$AGENT_NAMES_DEFAULT"

# Unified gateway: one port per agent, serves OpenAI + A2A + workspace.
AGENT_PORT=9001

# --- Config -----------------------------------------------------------------
FLEET_ROOT="${FLEET_ROOT:-/srv/hermes-fleet}"
ADAPTER_IMAGE="${ADAPTER_IMAGE:-ghcr.io/balaji-embedcentrum/hermes-adapter:latest}"
# Traefik v3.3+ required. Older v3.1 ships with a Docker SDK client that
# defaults to API version 1.24, which modern Docker Engine (25+) rejects
# with "client version 1.24 is too old. Minimum supported API version is
# 1.40". v3.3's SDK negotiates correctly.
TRAEFIK_IMAGE="${TRAEFIK_IMAGE:-traefik:latest}"

# --- 1. Docker sanity (bootstrap already ensured docker is present) --------
say "using $(docker --version)"

# --- 2. DNS sanity check ----------------------------------------------------
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

# --- 3. Make folders --------------------------------------------------------
SUDO=""; [ -w "$(dirname "$FLEET_ROOT")" ] || SUDO="sudo"
$SUDO mkdir -p "$FLEET_ROOT/workspaces" "$FLEET_ROOT/agents" "$FLEET_ROOT/letsencrypt"
$SUDO chown -R "$USER:$USER" "$FLEET_ROOT"
touch "$FLEET_ROOT/letsencrypt/acme.json" && chmod 600 "$FLEET_ROOT/letsencrypt/acme.json"
ok "install root: $FLEET_ROOT"

# --- 4. Pull or build images ------------------------------------------------
# Auto-detect: if this script is running from a cloned hermes-adapter repo
# (there's a Dockerfile one dir up), build the adapter locally so it
# includes whatever branch you're on (docker CLI + fleet mode, etc).
# Otherwise fall back to pulling from the registry.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" &>/dev/null && pwd || echo "")"
REPO_ROOT=""
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/../Dockerfile" ]; then
  REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
fi

if [ -n "$REPO_ROOT" ]; then
  ADAPTER_IMAGE="hermes-adapter:local"
  say "building adapter locally from $REPO_ROOT → $ADAPTER_IMAGE"
  docker build -t "$ADAPTER_IMAGE" "$REPO_ROOT"
else
  say "pulling $ADAPTER_IMAGE from registry"
  docker pull "$ADAPTER_IMAGE"
fi
docker pull "$TRAEFIK_IMAGE"

# --- 5. Generate the shared bearer key --------------------------------------
if [ -f "$FLEET_ROOT/.bearer-key" ]; then
  BEARER_KEY="$(cat "$FLEET_ROOT/.bearer-key")"
  warn "reusing existing bearer key at $FLEET_ROOT/.bearer-key"
else
  BEARER_KEY="$(openssl rand -hex 32)"
  echo "$BEARER_KEY" > "$FLEET_ROOT/.bearer-key"
  chmod 600 "$FLEET_ROOT/.bearer-key"
  ok "bearer key generated and saved to $FLEET_ROOT/.bearer-key"
fi

# Per-agent filebrowser admin password — used by every <name>-files.<domain>
# subdomain. Filebrowser's own auth (DB-backed) — admin/<password>. We bake
# the credential into a one-shot init container so the user never has to
# touch the filebrowser CLI.
if [ -f "$FLEET_ROOT/.fb-password" ]; then
  FB_PASSWORD="$(cat "$FLEET_ROOT/.fb-password")"
  warn "reusing existing filebrowser password at $FLEET_ROOT/.fb-password"
else
  FB_PASSWORD="$(openssl rand -hex 12)"
  echo "$FB_PASSWORD" > "$FLEET_ROOT/.fb-password"
  chmod 600 "$FLEET_ROOT/.fb-password"
  ok "filebrowser admin password generated and saved to $FLEET_ROOT/.fb-password"
fi

# --- 6. Stack-level .env ----------------------------------------------------
# FLEET_HOST_ROOT is the host-absolute path bind-mounted into the adapter
# container at /srv/hermes-fleet. The adapter needs this to find
# docker-compose.yml + write compose overrides when /fleet/claim runs.
cat > "$FLEET_ROOT/.env" <<EOF
DOMAIN=$DOMAIN
STUDIO_URL=$STUDIO_URL
BEARER_KEY=$BEARER_KEY
FB_PASSWORD=$FB_PASSWORD
ACME_EMAIL=$ACME_EMAIL
FLEET_HOST_ROOT=$FLEET_ROOT
EOF
chmod 600 "$FLEET_ROOT/.env"
ok "wrote $FLEET_ROOT/.env"

# Create the sentinel "unclaimed" workspace — an empty directory that
# agent containers mount as /opt/workspaces when no user has claimed
# them. Keeps the container's view empty instead of exposing siblings.
mkdir -p "$FLEET_ROOT/workspaces/_unclaimed"
chmod 755 "$FLEET_ROOT/workspaces/_unclaimed"
mkdir -p "$FLEET_ROOT/compose.override"
ok "fleet sentinel + override dir ready"

# --- 7. Per-agent skeleton folders ------------------------------------------
# When --personas-file is provided, also seed:
#   - agents/<name>/persona.md with the personality blurb (you flesh out later)
#   - agents/<name>/.persona-meta with display, role, skills (read by step 8)
for name in $AGENT_NAMES; do
  AGENT_DIR="$FLEET_ROOT/agents/$name"
  mkdir -p "$AGENT_DIR"

  # Pull richer metadata if --personas-file was given
  display="$name"; role=""; skills=""; personality=""
  if [ -n "$PERSONAS_FILE" ]; then
    display="$(jq -r --arg n "$name" '.[] | select(.name==$n) | .display      // .name' "$PERSONAS_FILE")"
    role="$(   jq -r --arg n "$name" '.[] | select(.name==$n) | .role         // ""'    "$PERSONAS_FILE")"
    skills="$( jq -r --arg n "$name" '.[] | select(.name==$n) | (.skills // [] | join(","))' "$PERSONAS_FILE")"
    personality="$(jq -r --arg n "$name" '.[] | select(.name==$n) | .personality // ""' "$PERSONAS_FILE")"
    # Stash for step 8 to read without re-parsing. Values are shell-quoted so
    # display names like "Anika Singh" don't get parsed as "command not found".
    cat > "$AGENT_DIR/.persona-meta" <<EOF
display=$(printf '%q' "$display")
role=$(printf '%q' "$role")
skills=$(printf '%q' "$skills")
EOF
  fi

  if [ ! -f "$AGENT_DIR/.env" ]; then
    cat > "$AGENT_DIR/.env" <<EOF
# Provider key for agent "$name". Fill in via:
#   ./fleet set $name --model <model> --key <key>
# Common provider env-var names: OPENROUTER_API_KEY, ANTHROPIC_API_KEY,
# OPENAI_API_KEY, TOGETHER_API_KEY, MINIMAX_API_KEY
EOF
    chmod 600 "$AGENT_DIR/.env"
  fi
  if [ ! -f "$AGENT_DIR/config.yaml" ]; then
    cat > "$AGENT_DIR/config.yaml" <<EOF
# Default model for agent "$name". Edit with:
#   ./fleet set $name --model <provider/model-id> --key <key>
model:
  default: openrouter/minimax/minimax-m2
EOF
  fi
  if [ -n "$personality" ] && [ ! -f "$AGENT_DIR/persona.md" ]; then
    cat > "$AGENT_DIR/persona.md" <<EOF
# $display

Role: $role

## Personality (one-line)

$personality

## Soul (TODO — flesh this out)

Write the system prompt that gives this agent its voice, beliefs,
quirks, and decision-making style. This file is for you; wire it
into the agent's system_prompt config when ready.
EOF
  fi
done
ok "scaffolded $(echo "$AGENT_NAMES" | wc -w) agent folders under $FLEET_ROOT/agents/"

# --- 8. Generate docker-compose.yml -----------------------------------------
COMPOSE="$FLEET_ROOT/docker-compose.yml"
cat > "$COMPOSE" <<'YAML'
name: hermes-fleet

x-agent-common: &agent-common
  image: ADAPTER_IMAGE_PLACEHOLDER
  restart: unless-stopped
  networks: [fleet]

services:
  traefik:
    image: TRAEFIK_IMAGE_PLACEHOLDER
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    # Pin the Docker Engine API version. Traefik v3's docker provider
    # defaults to an older API negotiation that modern Docker daemons
    # (v25+ installed fresh from get.docker.com) reject with
    # "client version too old. Minimum supported API version is 1.40".
    # Without this, Traefik fails to read container labels, no routes
    # register, and every HTTP request 404s through the fallback.
    environment:
      DOCKER_API_VERSION: "1.43"
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.websecure.address=:443
      - --entrypoints.web.http.redirections.entrypoint.to=websecure
      - --entrypoints.web.http.redirections.entrypoint.scheme=https
      - --certificatesresolvers.le.acme.email=${ACME_EMAIL}
      - --certificatesresolvers.le.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.le.acme.tlschallenge=true
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./letsencrypt:/letsencrypt
    networks: [fleet]

  adapter:
    image: ADAPTER_IMAGE_PLACEHOLDER
    restart: unless-stopped
    command: ["workspace"]
    environment:
      HERMES_ADAPTER_HOST: 0.0.0.0
      HERMES_ADAPTER_PORT: 8766
      HERMES_WORKSPACE_DIR: /workspaces
      HERMES_ADAPTER_CORS_ORIGINS: ${STUDIO_URL}
      # Fleet control plane — enables /fleet/claim, /fleet/unclaim,
      # /fleet/status. FLEET_ROOT must point at the host path mounted
      # below so adapter can read docker-compose.yml + write overrides.
      HERMES_FLEET_MODE: "1"
      FLEET_ROOT: /srv/hermes-fleet
      FLEET_CONTROL_KEY: ${BEARER_KEY}
    volumes:
      - ./workspaces:/workspaces
      # Fleet control needs the host's compose file + override dir,
      # plus the docker socket to run `docker compose up -d` against
      # agent containers. This is a privileged mount — treat adapter
      # as security-critical.
      - ${FLEET_HOST_ROOT}:/srv/hermes-fleet
      - /var/run/docker.sock:/var/run/docker.sock
    networks: [fleet]
    labels:
      - traefik.enable=true
      # /agent-<name>/ws/* — per-agent workspace proxy (read/write files,
      # git ops). Adapter receives stripped path.
      - traefik.http.routers.ws.rule=Host(`${DOMAIN}`) && PathRegexp(`^/agent-[a-z]+/ws`)
      - traefik.http.routers.ws.entrypoints=websecure
      - traefik.http.routers.ws.tls.certresolver=le
      - traefik.http.routers.ws.middlewares=ws-strip
      # Explicit router→service binding is REQUIRED when a single
      # container hosts multiple routers + multiple services. Without
      # it Traefik refuses with "Router ws cannot be linked
      # automatically with multiple Services".
      - traefik.http.routers.ws.service=ws
      - traefik.http.middlewares.ws-strip.replacepathregex.regex=^/agent-[a-z]+(/ws.*)$$
      - traefik.http.middlewares.ws-strip.replacepathregex.replacement=$$1
      - traefik.http.services.ws.loadbalancer.server.port=8766
      # /fleet/* — root-level control plane (claim/unclaim/status).
      # Bearer-auth'd in the handler; see hermes_adapter.fleet.routes.
      - traefik.http.routers.fleet.rule=Host(`${DOMAIN}`) && PathPrefix(`/fleet`)
      - traefik.http.routers.fleet.entrypoints=websecure
      - traefik.http.routers.fleet.tls.certresolver=le
      - traefik.http.routers.fleet.service=fleet
      - traefik.http.services.fleet.loadbalancer.server.port=8766

YAML

# substitute the image placeholders
sed -i.bak \
  -e "s|ADAPTER_IMAGE_PLACEHOLDER|$ADAPTER_IMAGE|g" \
  -e "s|TRAEFIK_IMAGE_PLACEHOLDER|$TRAEFIK_IMAGE|g" \
  "$COMPOSE"
rm -f "$COMPOSE.bak"

# Filebrowser basic-auth hash substitution moved below — must run AFTER
# the per-agent loop appends services that contain the placeholder.

# Append one service block per agent. Protocol-specific env keys plus
# (optional) AGENT_DESCRIPTION / AGENT_SKILLS sourced from persona metadata.
for name in $AGENT_NAMES; do
  # Pull persona metadata stashed in step 7 (display, role, skills)
  display="$name"; role=""; skills=""
  if [ -f "$FLEET_ROOT/agents/$name/.persona-meta" ]; then
    # shellcheck disable=SC1091
    . "$FLEET_ROOT/agents/$name/.persona-meta"
  fi
  agent_desc=""
  if [ -n "$role" ]; then
    agent_desc="${display} — ${role}"
  elif [ "$display" != "$name" ]; then
    agent_desc="$display"
  fi

cat >> "$COMPOSE" <<YAML
  hermes-agent-$name:
    <<: *agent-common
    container_name: hermes-agent-$name
    command: ["gateway"]
    env_file: ./agents/$name/.env
    environment:
      # Unified gateway binds one port; A2A_* env vars are reused by
      # the gateway's Starlette app for both the A2A and OpenAI routes.
      A2A_HOST: 0.0.0.0
      A2A_PORT: $AGENT_PORT
      A2A_KEY: \${BEARER_KEY}
      A2A_PUBLIC_URL: https://\${DOMAIN}/agent-$name
      # OpenAI-compat handler reads the same bearer.
      API_SERVER_KEY: \${BEARER_KEY}
      # Override upstream's HERMES_HOME=/opt/data — point at the
      # mounted agent dir so this agent's .env + config.yaml get loaded.
      HERMES_HOME: /root/.hermes
      # The base image installs hermes-agent via ``pip install -e .``
      # from /opt/hermes; our adapter pip install on top can break the
      # editable .pth so ``run_agent`` isn't importable. The gateway
      # falls back to inserting this path into sys.path at startup.
      HERMES_AGENT_ROOT: /opt/hermes
      AGENT_NAME: $name
      AGENT_DESCRIPTION: "$agent_desc"
      AGENT_SKILLS: "$skills"
      # Bind-mount pattern: each agent sees exactly one user's workspace
      # at /opt/workspaces after /fleet/claim writes its override.
      # Kernel-enforced isolation, no symlink tricks required.
      HERMES_WORKSPACE_DIR: /opt/workspaces
    volumes:
      # Default mount: the sentinel "unclaimed" dir — empty and
      # read-only. /fleet/claim writes a per-agent override at
      # $FLEET_ROOT/compose.override/$name.yml that replaces this
      # with ./workspaces/<user>:/opt/workspaces for the chosen user.
      # Unclaimed agents never see any user's files.
      - ./workspaces/_unclaimed:/opt/workspaces:ro
      - ./agents/$name:/root/.hermes
    profiles: ["agents"]
    labels:
      - traefik.enable=true
      - traefik.http.routers.$name.rule=Host(\`\${DOMAIN}\`) && PathPrefix(\`/agent-$name\`)
      - traefik.http.routers.$name.entrypoints=websecure
      - traefik.http.routers.$name.tls.certresolver=le
      - traefik.http.routers.$name.middlewares=$name-strip
      - traefik.http.middlewares.$name-strip.stripprefix.prefixes=/agent-$name
      - traefik.http.services.$name.loadbalancer.server.port=$AGENT_PORT

  # Per-agent filebrowser sidecar — exposes ONLY this agent's Hermes
  # home (sessions, memory, logs, persona.md, SOUL.md, config.yaml) at
  # https://$name-files.\${DOMAIN}. Mount is scoped to ./agents/$name
  # so this filebrowser cannot see sibling agents OR any user
  # workspace. Filebrowser itself runs --noauth; Traefik adds basic
  # auth in front (admin / \${FB_PASSWORD}). See /srv/hermes-fleet/.fb-password.
  hermes-fb-$name:
    image: filebrowser/filebrowser:latest
    container_name: hermes-fb-$name
    restart: unless-stopped
    user: "0:0"
    command:
      - --noauth
      - --root=/srv
      - --address=0.0.0.0
      - --port=80
    volumes:
      # ONLY this agent's home dir — sessions, memory, logs, persona.md,
      # SOUL.md, config.yaml. NOT user workspaces, NOT sibling agents.
      # Note: .env contains the provider API key in plaintext; treat the
      # filebrowser admin password as a secret of equivalent sensitivity.
      - ./agents/$name:/srv
    networks: [fleet]
    profiles: ["agents"]
    labels:
      - traefik.enable=true
      - traefik.http.routers.fb-$name.rule=Host(\`$name-files.\${DOMAIN}\`)
      - traefik.http.routers.fb-$name.entrypoints=websecure
      - traefik.http.routers.fb-$name.tls.certresolver=le
      - traefik.http.routers.fb-$name.service=fb-$name
      - traefik.http.routers.fb-$name.middlewares=fb-auth
      - traefik.http.services.fb-$name.loadbalancer.server.port=80
      # Shared basic-auth middleware (declared once, reused by every fb
      # router). Hash is openssl passwd -apr1 \${FB_PASSWORD} computed
      # at install time and injected by install-fleet.sh.
      - "traefik.http.middlewares.fb-auth.basicauth.users=admin:FB_AUTH_HASH_PLACEHOLDER"

YAML
done

# Networks block
cat >> "$COMPOSE" <<'YAML'
networks:
  fleet:
    driver: bridge
YAML

# Filebrowser basic-auth hash (apr1) — Traefik's basicauth middleware
# expects htpasswd-style "user:hash" pairs. The hash contains literal
# $-signs which compose interprets as variable refs unless we double
# them; do that here, then sed-substitute into every per-agent label.
# Must run AFTER the per-agent loop appends the placeholder.
FB_HASH="$(openssl passwd -apr1 "$FB_PASSWORD" | sed 's/\$/$$/g')"
sed -i.bak "s|FB_AUTH_HASH_PLACEHOLDER|$FB_HASH|g" "$COMPOSE"
rm -f "$COMPOSE.bak"

# validate
( cd "$FLEET_ROOT" && docker compose config >/dev/null )
ok "wrote $COMPOSE ($(wc -l < "$COMPOSE") lines)"

# --- 9. Install the ./fleet helper ------------------------------------------
FLEET_BIN="$FLEET_ROOT/fleet"
cat > "$FLEET_BIN" <<'BASH'
#!/usr/bin/env bash
# fleet — manage the Hermes Studio agent fleet
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "$HERE"

usage() {
  cat <<EOF
Usage: ./fleet <command> [args]

Commands:
  set <name> --model <provider/model-id> --key <key>
        Set the model + provider key for one agent. Picks the right env-var
        name from the model prefix (openrouter/* → OPENROUTER_API_KEY, etc).
  bootstrap
        Interactive walkthrough — prompts for model + key for every agent
        whose .env is still empty. Uses hidden input for the key.
  up
        Start every configured agent container.
  down
        Stop every agent container (Traefik + adapter keep running).
  status
        Show container status.
  logs <name>
        Tail logs for one agent.
  list
        List all agents and whether each has a key set.
EOF
}

# Map a model prefix to a provider env-var name
key_var_for_model() {
  case "$1" in
    openrouter/*)         echo "OPENROUTER_API_KEY" ;;
    anthropic/*)          echo "ANTHROPIC_API_KEY" ;;
    openai/*|gpt-*)       echo "OPENAI_API_KEY" ;;
    google/*|gemini/*)    echo "GEMINI_API_KEY" ;;
    together_ai/*)        echo "TOGETHER_API_KEY" ;;
    minimax/*)            echo "MINIMAX_API_KEY" ;;
    *)                    echo "PROVIDER_API_KEY" ;;
  esac
}

cmd_set() {
  local name="$1"; shift
  [ -d "agents/$name" ] || { echo "no agent named $name" >&2; exit 1; }
  local model="" key=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --model) model="$2"; shift 2 ;;
      --key)   key="$2";   shift 2 ;;
      *) echo "unknown flag: $1" >&2; exit 1 ;;
    esac
  done
  [ -n "$model" ] || { echo "--model required" >&2; exit 1; }
  if [ -z "$key" ]; then
    read -r -s -p "API key for $name (hidden): " key; echo
  fi
  local var; var="$(key_var_for_model "$model")"
  cat > "agents/$name/.env" <<EOF
$var=$key
EOF
  chmod 600 "agents/$name/.env"
  cat > "agents/$name/config.yaml" <<EOF
model:
  default: $model
EOF
  echo "✓ $name: model=$model, env-var=$var"
}

cmd_bootstrap() {
  for d in agents/*/; do
    local name; name="$(basename "$d")"
    if grep -qE '^[A-Z_]+_API_KEY=.+' "$d/.env" 2>/dev/null; then
      echo "↷ $name: already configured, skipping"
      continue
    fi
    echo "── $name ──────────────────────────────────────────"
    read -r -p "  model (e.g. openrouter/minimax/minimax-m2): " model
    [ -n "$model" ] || { echo "  skipped"; continue; }
    read -r -s -p "  API key (hidden): " key; echo
    cmd_set "$name" --model "$model" --key "$key"
  done
}

# compose_flags — assemble -f flags for the base compose file + every
# per-agent override the adapter has written via /fleet/claim. Must be
# passed to every docker compose invocation so the current bind mounts
# are preserved. Without this, `./fleet up` would silently revert
# claims back to the sentinel mount.
compose_flags() {
  local flags=("-f" "docker-compose.yml")
  if [ -d compose.override ]; then
    for o in compose.override/*.yml; do
      [ -f "$o" ] && flags+=("-f" "$o")
    done
  fi
  printf '%s\n' "${flags[@]}"
}

cmd_up()     { mapfile -t f < <(compose_flags); docker compose "${f[@]}" --profile agents up -d; docker compose "${f[@]}" ps; }
cmd_down()   { mapfile -t f < <(compose_flags); docker compose "${f[@]}" --profile agents stop; }
cmd_status() { mapfile -t f < <(compose_flags); docker compose "${f[@]}" ps; }
cmd_logs()   { mapfile -t f < <(compose_flags); docker compose "${f[@]}" logs -f "hermes-agent-$1"; }
cmd_list() {
  printf "%-12s %-8s %s\n" "AGENT" "KEYED" "MODEL"
  for d in agents/*/; do
    local name; name="$(basename "$d")"
    local keyed="no"
    grep -qE '^[A-Z_]+_API_KEY=.+' "$d/.env" 2>/dev/null && keyed="yes"
    local model; model="$(awk '/default:/ {print $2}' "$d/config.yaml" 2>/dev/null || echo "?")"
    printf "%-12s %-8s %s\n" "$name" "$keyed" "$model"
  done
}

[ $# -ge 1 ] || { usage; exit 0; }
case "$1" in
  set)        shift; cmd_set "$@" ;;
  bootstrap)  cmd_bootstrap ;;
  up)         cmd_up ;;
  down)       cmd_down ;;
  status)     cmd_status ;;
  logs)       shift; cmd_logs "$@" ;;
  list)       cmd_list ;;
  -h|--help)  usage ;;
  *) echo "unknown command: $1" >&2; usage; exit 1 ;;
esac
BASH
chmod +x "$FLEET_BIN"
ok "installed $FLEET_BIN"

# --- 10. Start Traefik + adapter (NOT the agents yet) -----------------------
say "starting Traefik + adapter (agents stay down until keys are set)"
( cd "$FLEET_ROOT" && docker compose up -d traefik adapter )

# --- 11. Print next steps ---------------------------------------------------
cat <<EOF

${C_GREEN}${C_BOLD}═══ Fleet installed ═══${C_RESET}

Domain:         ${C_CYAN}https://$DOMAIN${C_RESET}
Bearer key:     ${C_CYAN}$FLEET_ROOT/.bearer-key${C_RESET}
Filebrowser:    ${C_CYAN}https://<name>-files.$DOMAIN${C_RESET}  (admin / $(cat "$FLEET_ROOT/.fb-password"))
Stack root:     ${C_CYAN}$FLEET_ROOT${C_RESET}
Helper:         ${C_CYAN}$FLEET_ROOT/fleet${C_RESET}

${C_BOLD}Next steps${C_RESET}

  ${C_BOLD}1.${C_RESET} Fill in model + key for each agent (interactive, hidden input):

       cd $FLEET_ROOT
       ./fleet bootstrap

     Or set them one at a time:

       ./fleet set emma   --model openrouter/minimax/minimax-m2
       ./fleet set mateo  --model openrouter/minimax/minimax-m2

  ${C_BOLD}2.${C_RESET} Start the 10 agent containers:

       ./fleet up

  ${C_BOLD}3.${C_RESET} Verify:

       ./fleet status
       curl -sf https://$DOMAIN/agent-emma/v1/models \\
            -H "Authorization: Bearer \$(cat $FLEET_ROOT/.bearer-key)"

  ${C_BOLD}4.${C_RESET} Register all 10 in Hermes Studio's Supabase \`agent_instances\` table.
     URLs follow this pattern (one row per agent):

       api_url    = https://$DOMAIN/agent-<name>
       api_key    = <bearer key from step above>
       model_name = <whatever you set in step 1>

EOF
