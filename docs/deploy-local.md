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

## Step 3 — Per-agent configs (each agent picks its own model)

Each agent gets its own `HERMES_HOME` directory. That directory holds `.env` and `config.yaml` for **that agent only** — model, provider key, persona, toolsets. The adapter never touches these.

```bash
mkdir -p ~/hermes-workspaces ~/hermes-agents/{alpha,beta,gamma}
```

Now give each agent its own config. Example: `alpha` does code review on Claude Sonnet, `beta` does fast triage on a local Llama via Ollama, `gamma` does research on Gemini.

**`~/hermes-agents/alpha/.env`** — Claude Sonnet
```bash
cat > ~/hermes-agents/alpha/.env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
A2A_KEY=local-dev-key-change-me
EOF
chmod 600 ~/hermes-agents/alpha/.env

cat > ~/hermes-agents/alpha/config.yaml <<'EOF'
model:
  default: anthropic/claude-sonnet-4.6
EOF
```

**`~/hermes-agents/beta/.env`** — local Llama via Ollama
```bash
cat > ~/hermes-agents/beta/.env <<'EOF'
OPENAI_API_KEY=dummy
OPENAI_BASE_URL=http://localhost:11434/v1
A2A_KEY=local-dev-key-change-me
EOF
chmod 600 ~/hermes-agents/beta/.env

cat > ~/hermes-agents/beta/config.yaml <<'EOF'
model:
  default: openai/llama3.1
EOF
```

**`~/hermes-agents/gamma/.env`** — Gemini
```bash
cat > ~/hermes-agents/gamma/.env <<'EOF'
GEMINI_API_KEY=...
A2A_KEY=local-dev-key-change-me
EOF
chmod 600 ~/hermes-agents/gamma/.env

cat > ~/hermes-agents/gamma/config.yaml <<'EOF'
model:
  default: google/gemini-2.0-flash
EOF
```

Only `A2A_KEY` is shared — because Studio sends the same bearer when calling any of the three.

The adapter itself needs **no** model config. It only cares about the workspace root (step 5).

## Step 4 — Smoke test one agent's config

Pick any of the three and run `hermes chat` against its personal config:

```bash
HERMES_HOME=~/hermes-agents/alpha hermes chat
```

Type `hi`, press Enter. If you get a reply, press `Ctrl+D` to quit.
Repeat for `beta` and `gamma` to confirm each agent's key/model works on its own.
If any one fails, nothing else below will work for that agent — fix its `.env`/`config.yaml` first.

## Step 5 — Start the shared workspace adapter

Open **Terminal 1**. The adapter takes **no** model keys — only the workspace root.

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

## Step 6 — Start each agent with its own HERMES_HOME

Each agent picks up its own provider key and model from its own `HERMES_HOME`. Open one terminal per agent.

**Terminal 2 — alpha (Claude)**
```bash
source ~/.hermes-venv/bin/activate
export HERMES_HOME=~/hermes-agents/alpha
export AGENT_NAME=alpha
export AGENT_DESCRIPTION="Code review (Claude Sonnet)"
export A2A_PORT=9001
hermes-a2a
```

**Terminal 3 — beta (local Llama)**
```bash
source ~/.hermes-venv/bin/activate
export HERMES_HOME=~/hermes-agents/beta
export AGENT_NAME=beta
export AGENT_DESCRIPTION="Fast triage (local Llama)"
export A2A_PORT=9002
hermes-a2a
```

**Terminal 4 — gamma (Gemini)**
```bash
source ~/.hermes-venv/bin/activate
export HERMES_HOME=~/hermes-agents/gamma
export AGENT_NAME=gamma
export AGENT_DESCRIPTION="Research (Gemini)"
export A2A_PORT=9003
hermes-a2a
```

Each agent talks to a different LLM, with a different key, a different model, a different persona — and they all answer on different ports. Add more agents by copying a folder under `~/hermes-agents/`, editing its `.env` + `config.yaml`, and starting another terminal.

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

mkdir -p ~/hermes-agents/logs

# Shared adapter — no model config needed
hermes-adapter workspace --host 127.0.0.1 --port 8766 \
  > ~/hermes-agents/logs/adapter.log 2>&1 &

# Each agent points at its own HERMES_HOME, so its own key/model/persona
for spec in "alpha:9001" "beta:9002" "gamma:9003"; do
  name="${spec%%:*}"
  port="${spec##*:}"
  HERMES_HOME=~/hermes-agents/"$name" \
    AGENT_NAME="$name" \
    A2A_PORT="$port" \
    nohup hermes-a2a \
    > ~/hermes-agents/logs/"$name".log 2>&1 &
done

sleep 2
echo "── running ──"
ps -ef | grep -E "hermes-(a2a|adapter)" | grep -v grep
echo
echo "Logs: ~/hermes-agents/logs/"
echo "Stop:  pkill -f 'hermes-(a2a|adapter)'"
```

```bash
chmod +x ~/bin/hermes-up
hermes-up
```

To stop everything: `pkill -f 'hermes-(a2a|adapter)'`.

---

## Reference: which env vars matter where

Two clean groups — per-agent (LLM stuff, lives in each agent's `HERMES_HOME/.env`) and shared (adapter stuff, set in the terminal that starts the adapter).

### Per-agent (one set per `hermes-a2a` process)

| Variable | What it does |
|---|---|
| `HERMES_HOME` | Points hermes-agent at this agent's config folder. **Drives everything below.** |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` / `OPENROUTER_API_KEY` | The one key that matches **this agent's** chosen provider. Inside `HERMES_HOME/.env`. |
| `OPENAI_BASE_URL` | Points at a local / self-hosted OpenAI-compatible endpoint (Ollama, vLLM, LM Studio). |
| `A2A_PORT` | Which port this agent listens on |
| `AGENT_NAME`, `AGENT_DESCRIPTION` | What shows up in this agent's Agent Card |
| `A2A_KEY` | Bearer token callers must present to this agent (can be per-agent or shared across all agents — your call) |

Model + provider are picked per-agent via `HERMES_HOME/config.yaml` (`model.default: anthropic/claude-sonnet-4.6`, etc.).

### Adapter (one set, shared)

| Variable | What it does |
|---|---|
| `HERMES_WORKSPACE_DIR` | Root folder where all agents' repos live |
| `HERMES_ADAPTER_HOST`, `HERMES_ADAPTER_PORT` | Where the workspace API listens |

**The adapter takes zero model config.** It never calls an LLM — it only reads/writes files and runs git. Model keys belong inside each agent's `HERMES_HOME`, not in the adapter's environment.

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
