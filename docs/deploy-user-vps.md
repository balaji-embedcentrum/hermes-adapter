# User's own VPS — single-tenant agent host for hermes-studio.com

This is tier 2 of the [three-tier model](agent-sources.md). Use it when:

- You're a single user who wants your agents always online (even when your laptop is closed)
- You want to run heavier models than your laptop can handle
- You want multiple personal devices to talk to the same agents

You rent any $5–$20 VPS (Hetzner CX22, DO $6 droplet, whatever). You install `hermes-adapter` + `hermes-agent` there. You point `hermes-studio.com` at your VPS's HTTPS URL. Done.

Not the same as [deploy-vps.md](deploy-vps.md) — that doc is for the **platform operator** running many tenants. This one is for **one user, one VPS**.

## The shape you're building

```
┌───────────────────────────────────────────────────────┐
│ Your VPS (alice.example.com)                          │
│                                                       │
│   Caddy :443 (TLS, reverse proxy)                     │
│       ├── /ws/*        → adapter     :8766           │
│       ├── /a2a/alpha/* → hermes-a2a  :9001           │
│       └── /a2a/beta/*  → hermes-a2a  :9002           │
│                                                       │
│   All three behind one hostname, one TLS cert.        │
└───────────────────────────────────────────────────────┘
              ▲
              │ fetch() from your browser, JS served by hermes-studio.com
              │
       ┌─────────────────────────────┐
       │ hermes-studio.com (SaaS)    │
       └─────────────────────────────┘
```

## Step 1 — Rent a VPS and SSH in

Ubuntu 24.04 or similar. You need: Python 3.11+, git, Caddy.

```bash
ssh alice@alice.example.com
sudo apt update
sudo apt install -y python3.12 python3.12-venv git
sudo apt install -y caddy
```

Point a DNS A record `alice.example.com → <VPS-IP>` before continuing — Caddy auto-provisions TLS from Let's Encrypt, but needs the DNS to resolve first.

## Step 2 — Install hermes-agent + hermes-adapter

```bash
python3.12 -m venv ~/.hermes-venv
source ~/.hermes-venv/bin/activate
pip install --upgrade pip

git clone https://github.com/NousResearch/hermes-agent ~/hermes-agent
pip install -e '~/hermes-agent[a2a]'

git clone https://github.com/balaji-embedcentrum/hermes-adapter ~/hermes-adapter
pip install -e '~/hermes-adapter[a2a]'
```

## Step 3 — Per-agent configs

Same pattern as [deploy-local.md](deploy-local.md) — each agent gets its own `HERMES_HOME`. Example: two agents.

```bash
mkdir -p ~/hermes-workspaces ~/hermes-agents/{alpha,beta}

# alpha — Claude Sonnet
cat > ~/hermes-agents/alpha/.env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
A2A_KEY=YOUR-LONG-RANDOM-BEARER-TOKEN
EOF
chmod 600 ~/hermes-agents/alpha/.env
cat > ~/hermes-agents/alpha/config.yaml <<'EOF'
model:
  default: anthropic/claude-sonnet-4.6
EOF

# beta — local Llama via Ollama (runs on the same VPS)
cat > ~/hermes-agents/beta/.env <<'EOF'
OPENAI_API_KEY=dummy
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
A2A_KEY=YOUR-LONG-RANDOM-BEARER-TOKEN
EOF
chmod 600 ~/hermes-agents/beta/.env
cat > ~/hermes-agents/beta/config.yaml <<'EOF'
model:
  default: openai/llama3.1:70b
EOF
```

The `A2A_KEY` must be the same across agents because Studio only stores one bearer per adapter. Generate it once:

```bash
openssl rand -hex 32
```

## Step 4 — systemd units (agents + adapter as services)

So they survive VPS reboots. Three services: one adapter, one per agent.

```bash
sudo tee /etc/systemd/system/hermes-adapter.service >/dev/null <<'EOF'
[Unit]
Description=hermes-adapter (workspace API)
After=network-online.target

[Service]
User=alice
Environment=HERMES_WORKSPACE_DIR=/home/alice/hermes-workspaces
Environment=HERMES_ADAPTER_HOST=127.0.0.1
Environment=HERMES_ADAPTER_PORT=8766
Environment=HERMES_ADAPTER_CORS_ORIGINS=https://hermes-studio.com
ExecStart=/home/alice/.hermes-venv/bin/hermes-adapter workspace
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/hermes-agent-alpha.service >/dev/null <<'EOF'
[Unit]
Description=hermes-a2a alpha
After=network-online.target

[Service]
User=alice
Environment=HERMES_HOME=/home/alice/hermes-agents/alpha
Environment=A2A_HOST=127.0.0.1
Environment=A2A_PORT=9001
Environment=AGENT_NAME=alpha
Environment=AGENT_DESCRIPTION=Code review (Claude Sonnet)
ExecStart=/home/alice/.hermes-venv/bin/hermes-a2a
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/hermes-agent-beta.service >/dev/null <<'EOF'
[Unit]
Description=hermes-a2a beta
After=network-online.target

[Service]
User=alice
Environment=HERMES_HOME=/home/alice/hermes-agents/beta
Environment=A2A_HOST=127.0.0.1
Environment=A2A_PORT=9002
Environment=AGENT_NAME=beta
Environment=AGENT_DESCRIPTION=Fast local Llama
ExecStart=/home/alice/.hermes-venv/bin/hermes-a2a
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now hermes-adapter hermes-agent-alpha hermes-agent-beta
```

Everything binds to `127.0.0.1` — not the public internet. Caddy fronts them.

## Step 5 — Caddy config (TLS + single-host routing)

```bash
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
alice.example.com {
    handle_path /ws/* {
        reverse_proxy 127.0.0.1:8766
    }
    handle /ws { reverse_proxy 127.0.0.1:8766 }  # exact match, no trailing slash

    handle_path /a2a/alpha/* {
        reverse_proxy 127.0.0.1:9001
    }
    handle_path /a2a/beta/* {
        reverse_proxy 127.0.0.1:9002
    }
}
EOF
sudo systemctl reload caddy
```

Caddy automatically gets a Let's Encrypt cert on first request. `handle_path` strips the prefix so the backend sees the canonical path (`/ws`, `/`, `/.well-known/...`).

## Step 6 — Verify from outside

From your laptop:

```bash
# Workspace API
curl -sf https://alice.example.com/ws | jq

# Agent alpha
curl -sf -H "Authorization: Bearer YOUR-A2A-KEY" \
  https://alice.example.com/a2a/alpha/.well-known/agent.json | jq
```

Both should return JSON.

## Step 7 — Wire it into hermes-studio.com

Log in at `hermes-studio.com`, open **Settings → My agents**, and fill in:

| Field | Value |
|---|---|
| Adapter URL | `https://alice.example.com/ws` *(or whatever Studio calls the workspace endpoint — see [integration-studio.md](integration-studio.md))* |
| A2A bearer | `YOUR-LONG-RANDOM-BEARER-TOKEN` |
| Agents | `alpha=https://alice.example.com/a2a/alpha`, `beta=https://alice.example.com/a2a/beta` |

Save. Studio's browser JS now calls your VPS directly. Studio's servers never see your traffic.

## Updates, backups, logs

| Task | Command |
|---|---|
| Tail adapter logs | `journalctl -u hermes-adapter -f` |
| Tail agent logs | `journalctl -u hermes-agent-alpha -f` |
| Update hermes-adapter | `cd ~/hermes-adapter && git pull && pip install -e '.[a2a]' && sudo systemctl restart hermes-adapter` |
| Update hermes-agent | `cd ~/hermes-agent && git pull && pip install -e '.[a2a]' && sudo systemctl restart 'hermes-agent-*'` |
| Rotate A2A key | Edit every agent's `.env`, restart services, update token in Studio settings |

## Why this is not the same as deploy-vps.md

The platform guide ([deploy-vps.md](deploy-vps.md)) is multi-tenant: many users, per-tenant isolation, `/ws/activate` symlink switching, Traefik labels for dozens of agents. You don't need any of that for your own single-user VPS — one Caddyfile and three systemd units is sufficient, and you can add agents by copying unit files.

If you ever want to host agents for friends too, graduate to the platform guide.
