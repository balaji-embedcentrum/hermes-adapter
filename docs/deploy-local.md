# Local setup — hermes agents + hermes-adapter + Hermes Studio

For a single laptop with a handful of agents. Everything runs on localhost. No Docker required.

## The shape you're building

```
┌─────────────────────────────────────────────────────────────┐
│ Your laptop                                                  │
│                                                              │
│   Hermes Studio (localhost:3000)                             │
│       │                                                      │
│       ├── workspace:  http://localhost:8766/ws/*  ──┐         │
│       └── chat:       http://localhost:9001..9003 ─┼─┐        │
│                                                   ▼ ▼        │
│   hermes-adapter                 hermes-a2a      hermes-a2a   │
│   workspace only :8766           agent-alpha     agent-beta   │
│                                  :9001           :9002        │
│                                                               │
│                       shared folder: ~/hermes-workspaces/     │
│                       shared config: ~/.hermes/               │
└─────────────────────────────────────────────────────────────┘
```

One adapter, N agents, each on their own port. Studio talks to all of them.

---

## Step 1 — One Python sandbox for everything

```bash
brew install python@3.12    # if you don't already have 3.11+

python3.12 -m venv ~/.hermes-venv
source ~/.hermes-venv/bin/activate
pip install --upgrade pip
```

From here on, every terminal that runs a hermes command needs
`source ~/.hermes-venv/bin/activate` first.

## Step 2 — Install hermes-agent + the adapter into the same sandbox

```bash
cd ~/Documents
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
pip install -e '.[a2a]'

cd ~/Documents
git clone https://github.com/balaji-embedcentrum/hermes-adapter.git
cd hermes-adapter
pip install -e '.[a2a]'
```

## Step 3 — One shared config

```bash
mkdir -p ~/.hermes ~/hermes-workspaces

cat > ~/.hermes/.env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-replace-me
A2A_KEY=local-dev-key-change-me
EOF
chmod 600 ~/.hermes/.env
```

Every agent and the adapter read from `~/.hermes/.env` automatically.

## Step 4 — Smoke test hermes itself

```bash
hermes chat
```

Type `hi`, press Enter. If you get a reply, press `Ctrl+D` to quit.
If it fails, nothing else below will work — fix this first.

## Step 5 — Start the shared workspace adapter

Open **Terminal 1**:

```bash
source ~/.hermes-venv/bin/activate
export HERMES_WORKSPACE_DIR=~/hermes-workspaces
hermes-adapter workspace --host 127.0.0.1 --port 8766
```

You should see:

```
workspace API listening on http://127.0.0.1:8766
workspace root: /Users/you/hermes-workspaces
```

Leave it running. Test from a second terminal:

```bash
curl http://127.0.0.1:8766/health
# → {"status":"ok","service":"hermes-adapter-workspace"}
```

## Step 6 — Start each agent on its own port

Open **Terminal 2** (agent `alpha`):

```bash
source ~/.hermes-venv/bin/activate
export AGENT_NAME=alpha
export AGENT_DESCRIPTION="Generic hermes agent (alpha)"
export A2A_PORT=9001
hermes-a2a
```

Open **Terminal 3** (agent `beta`):

```bash
source ~/.hermes-venv/bin/activate
export AGENT_NAME=beta
export AGENT_DESCRIPTION="Generic hermes agent (beta)"
export A2A_PORT=9002
hermes-a2a
```

Repeat per agent — each gets its own port. Typical local setup is 2–5 agents.

Verify:

```bash
curl -s http://localhost:9001/.well-known/agent.json | jq .name   # → "alpha"
curl -s http://localhost:9002/.well-known/agent.json | jq .name   # → "beta"
```

## Step 7 — Make a test workspace

```bash
mkdir -p ~/hermes-workspaces/me/myproject
cd ~/hermes-workspaces/me/myproject
git init -b main
echo "# hello" > README.md
git add . && git commit -m "first"

curl http://127.0.0.1:8766/ws | jq
# → includes "me/myproject"
```

## Step 8 — Point Hermes Studio at it

In Studio's `.env.local`:

```env
HERMES_ADAPTER_URL=http://127.0.0.1:8766
HERMES_A2A_AGENTS=alpha=http://127.0.0.1:9001,beta=http://127.0.0.1:9002
HERMES_A2A_KEY=local-dev-key-change-me
```

Start Studio (`pnpm dev` / `npm run dev`) and open http://localhost:3000. You should see:
- Your workspaces listed (from the adapter)
- A dropdown to pick `alpha` or `beta` (from the A2A list)
- Chat works against the selected agent

---

## A single script to start everything

Save as `~/bin/hermes-up`:

```bash
#!/usr/bin/env bash
set -euo pipefail

source ~/.hermes-venv/bin/activate
export HERMES_WORKSPACE_DIR=~/hermes-workspaces

# Kill any leftovers so restarts are clean
pkill -f "hermes-adapter workspace" || true
pkill -f "hermes-a2a" || true
sleep 1

mkdir -p ~/.hermes/logs

hermes-adapter workspace --host 127.0.0.1 --port 8766 \
  > ~/.hermes/logs/adapter.log 2>&1 &

for spec in "alpha:9001" "beta:9002" "gamma:9003"; do
  name="${spec%%:*}"
  port="${spec##*:}"
  AGENT_NAME="$name" A2A_PORT="$port" nohup hermes-a2a \
    > ~/.hermes/logs/"$name".log 2>&1 &
done

sleep 2
echo "── running ──"
ps -ef | grep -E "hermes-(a2a|adapter)" | grep -v grep
echo
echo "Logs: ~/.hermes/logs/"
echo "Stop:  pkill -f 'hermes-(a2a|adapter)'"
```

```bash
chmod +x ~/bin/hermes-up
hermes-up
```

To stop everything: `pkill -f 'hermes-(a2a|adapter)'`.

---

## Reference: which env vars matter where

| Variable | Used by | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` (or equivalent) | every `hermes-a2a` | Lets the agent call the LLM |
| `A2A_PORT` | `hermes-a2a` | Which port this agent listens on |
| `AGENT_NAME`, `AGENT_DESCRIPTION` | `hermes-a2a` | What shows up in the Agent Card |
| `A2A_KEY` | `hermes-a2a` (optional) | Bearer token callers must present |
| `HERMES_WORKSPACE_DIR` | `hermes-adapter workspace` | Root folder for all workspaces |
| `HERMES_ADAPTER_PORT` | `hermes-adapter workspace` | Port the workspace API listens on |

Anything in `~/.hermes/.env` is auto-loaded by both commands.

---

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: a2a` | No A2A extras | `pip install -e '.[a2a]'` in the adapter folder |
| `Address already in use` | Port collision | Change `A2A_PORT` to a free port |
| Studio sees agents but chat 401s | Wrong `A2A_KEY` | Match exactly between agent env and Studio config |
| Studio lists zero workspaces | Wrong path | `echo $HERMES_WORKSPACE_DIR` must match the folder you put repos in |
| `hermes-agent is not importable` when A2A starts | Sandbox mismatch | Reinstall hermes-agent in the **same** venv as the adapter |

---

## When to move from local to VPS

You're ready for [deploy-vps.md](deploy-vps.md) once you:

- Want Studio reachable off your laptop
- Want always-on agents (systemd / Docker restart policies)
- Need TLS (real certs via Traefik)
- Need to run 10+ agents (laptops handle 2–5 comfortably)

The local layout maps 1:1 to the VPS layout — same one-adapter-many-agents shape, just containerized.
