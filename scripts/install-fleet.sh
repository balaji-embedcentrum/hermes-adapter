#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# hermes-adapter — agent fleet installer (one-shot)
#
# Sets up an agent fleet on a fresh VPS, in either of two flavours:
#   --protocol openai  (default) — for Hermes Studio
#         hermes-agent serves the OpenAI-compatible API on /v1/*
#   --protocol a2a                — for Akela (or any A2A orchestrator)
#         hermes-agent serves the Agent-to-Agent JSON-RPC protocol at root
#
# Both flavours bring up:
#   - 1× Traefik    (TLS via Let's Encrypt)
#   - 1× adapter    (workspace API only — /ws/* paths, shared)
#   - N× hermes-agent containers (one per persona)
#
# After install, you fill in each agent's model + key with the generated
# `./fleet` helper, then `./fleet up` starts the agent containers.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install-fleet.sh \
#     | bash -s -- \
#         --domain agents.example.com \
#         --acme-email you@example.com
#
# Flags:
#   --domain         REQUIRED. Public hostname of the agent VPS (DNS A record
#                    must already point at this host).
#   --acme-email     REQUIRED. Email used by Let's Encrypt for cert renewals.
#   --protocol       OPTIONAL. openai | a2a (default: openai).
#   --studio-url     OPTIONAL. CORS origin for Studio (default: omit; only
#                    needed for openai-protocol fleets that browser-call
#                    the adapter directly).
#   --names          OPTIONAL. Space-separated agent names. Mutually
#                    exclusive with --personas-file.
#                    (default: "emma mateo aarav mei lea sofia yuki priya lukas diego")
#   --personas-file  OPTIONAL. Path to a JSON array of richer persona
#                    objects: [{name, display, role, skills[], personality},
#                    ...]. When present, populates AGENT_NAME /
#                    AGENT_DESCRIPTION / AGENT_SKILLS env so each agent's
#                    /.well-known/agent.json (a2a) or model card (openai)
#                    is rendered correctly. Also seeds agents/<name>/persona.md
#                    with the personality blurb for you to flesh out.
#
# Env overrides:
#   FLEET_ROOT       install root           (default: /srv/hermes-fleet)
#   ADAPTER_IMAGE    workspace API image
#   AGENT_IMAGE      hermes-agent image
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

# --- Parse flags ------------------------------------------------------------
DOMAIN=""
ACME_EMAIL=""
STUDIO_URL=""
PROTOCOL="openai"
PERSONAS_FILE=""
AGENT_NAMES_DEFAULT="emma mateo aarav mei lea sofia yuki priya lukas diego"
AGENT_NAMES=""

while [ $# -gt 0 ]; do
  case "$1" in
    --domain)         DOMAIN="$2";         shift 2 ;;
    --acme-email)     ACME_EMAIL="$2";     shift 2 ;;
    --studio-url)     STUDIO_URL="$2";     shift 2 ;;
    --protocol)       PROTOCOL="$2";       shift 2 ;;
    --personas-file)  PERSONAS_FILE="$2";  shift 2 ;;
    --names)          AGENT_NAMES="$2";    shift 2 ;;
    -h|--help)
      sed -n '2,49p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) die "unknown flag: $1 (try --help)" ;;
  esac
done

[ -n "$DOMAIN" ]     || die "missing required --domain"
[ -n "$ACME_EMAIL" ] || die "missing required --acme-email"
[[ "$PROTOCOL" == "openai" || "$PROTOCOL" == "a2a" ]] \
  || die "--protocol must be 'openai' or 'a2a' (got: $PROTOCOL)"
[ -z "$PERSONAS_FILE" ] || [ -z "$AGENT_NAMES" ] \
  || die "--personas-file and --names are mutually exclusive"
[ -z "$PERSONAS_FILE" ] || [ -f "$PERSONAS_FILE" ] \
  || die "--personas-file not found: $PERSONAS_FILE"

# When --personas-file is given, jq is required to parse it
if [ -n "$PERSONAS_FILE" ]; then
  command -v jq >/dev/null \
    || die "jq is required when using --personas-file. Install: sudo apt-get install -y jq"
  AGENT_NAMES="$(jq -r '.[].name' "$PERSONAS_FILE" | tr '\n' ' ')"
  [ -n "$AGENT_NAMES" ] || die "no agents found in $PERSONAS_FILE"
fi
[ -n "$AGENT_NAMES" ] || AGENT_NAMES="$AGENT_NAMES_DEFAULT"

# Per-protocol settings (the agent's internal port + which key env it reads)
if [ "$PROTOCOL" = "a2a" ]; then
  AGENT_PORT=9000
  AGENT_KEY_ENV="A2A_KEY"
  AGENT_COMMAND='["a2a"]'
else
  AGENT_PORT=8642
  AGENT_KEY_ENV="API_SERVER_KEY"
  AGENT_COMMAND='["gateway"]'
fi

# --- Config -----------------------------------------------------------------
FLEET_ROOT="${FLEET_ROOT:-/srv/hermes-fleet}"
ADAPTER_IMAGE="${ADAPTER_IMAGE:-ghcr.io/balaji-embedcentrum/hermes-adapter:latest}"
AGENT_IMAGE="${AGENT_IMAGE:-nousresearch/hermes-agent:latest}"
TRAEFIK_IMAGE="${TRAEFIK_IMAGE:-traefik:v3.1}"

# --- 1. Docker check --------------------------------------------------------
command -v docker >/dev/null      || die "Docker not found. Install: curl -fsSL https://get.docker.com | sh"
docker compose version >/dev/null || die "Docker Compose v2 missing. Install the docker-compose-plugin package."
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

# --- 4. Pull images ---------------------------------------------------------
say "pulling images"
docker pull "$ADAPTER_IMAGE"
docker pull "$AGENT_IMAGE"
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

# --- 6. Stack-level .env ----------------------------------------------------
cat > "$FLEET_ROOT/.env" <<EOF
DOMAIN=$DOMAIN
STUDIO_URL=$STUDIO_URL
BEARER_KEY=$BEARER_KEY
ACME_EMAIL=$ACME_EMAIL
EOF
chmod 600 "$FLEET_ROOT/.env"
ok "wrote $FLEET_ROOT/.env"

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
  image: AGENT_IMAGE_PLACEHOLDER
  restart: unless-stopped
  networks: [fleet]

services:
  traefik:
    image: TRAEFIK_IMAGE_PLACEHOLDER
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
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
    volumes:
      - ./workspaces:/workspaces
    networks: [fleet]
    labels:
      - traefik.enable=true
      - traefik.http.routers.ws.rule=Host(`${DOMAIN}`) && PathRegexp(`^/agent-[a-z]+/ws`)
      - traefik.http.routers.ws.entrypoints=websecure
      - traefik.http.routers.ws.tls.certresolver=le
      - traefik.http.routers.ws.middlewares=ws-strip
      - traefik.http.middlewares.ws-strip.replacepathregex.regex=^/agent-[a-z]+(/ws.*)$$
      - traefik.http.middlewares.ws-strip.replacepathregex.replacement=$$1
      - traefik.http.services.ws.loadbalancer.server.port=8766

YAML

# substitute the image placeholders
sed -i.bak \
  -e "s|AGENT_IMAGE_PLACEHOLDER|$AGENT_IMAGE|g" \
  -e "s|ADAPTER_IMAGE_PLACEHOLDER|$ADAPTER_IMAGE|g" \
  -e "s|TRAEFIK_IMAGE_PLACEHOLDER|$TRAEFIK_IMAGE|g" \
  "$COMPOSE"
rm -f "$COMPOSE.bak"

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

  if [ "$PROTOCOL" = "a2a" ]; then
    proto_env="$(cat <<ENV
      A2A_HOST: 0.0.0.0
      A2A_PORT: $AGENT_PORT
      A2A_KEY: \${BEARER_KEY}
ENV
    )"
  else
    proto_env="$(cat <<ENV
      API_SERVER_ENABLED: "true"
      API_SERVER_HOST: 0.0.0.0
      API_SERVER_PORT: $AGENT_PORT
      API_SERVER_KEY: \${BEARER_KEY}
ENV
    )"
  fi

cat >> "$COMPOSE" <<YAML
  hermes-agent-$name:
    <<: *agent-common
    container_name: hermes-agent-$name
    command: $AGENT_COMMAND
    env_file: ./agents/$name/.env
    environment:
$proto_env
      AGENT_NAME: $name
      AGENT_DESCRIPTION: "$agent_desc"
      AGENT_SKILLS: "$skills"
    volumes:
      - ./workspaces:/workspaces
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

YAML
done

# Networks block
cat >> "$COMPOSE" <<'YAML'
networks:
  fleet:
    driver: bridge
YAML

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

cmd_up()     { docker compose --profile agents up -d; docker compose ps; }
cmd_down()   { docker compose --profile agents stop; }
cmd_status() { docker compose ps; }
cmd_logs()   { docker compose logs -f "hermes-agent-$1"; }
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
