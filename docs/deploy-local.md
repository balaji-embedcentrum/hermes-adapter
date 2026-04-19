# Local agents + hosted Hermes Studio (zero to running)

**Scenario.** You're anywhere in the world. Hermes Studio is already deployed at `https://hermes-studio.com`. You want to run your own agents — one, five, ten — on your laptop and use Studio as the UI. No Docker. No per-agent terminals.

You run **one installer**, then use `hermes-adapter` commands for everything else. The adapter itself becomes the supervisor that manages all your local `hermes-a2a` processes.

---

## The shape you're building

```
┌───────────────────────────────────────────────┐       ┌─────────────────────────┐
│ Your laptop                                    │       │ hermes-studio.com       │
│                                                │       │ (serves UI JS only —    │
│   Browser, page from hermes-studio.com   ◄─────┼──────►│  never touches agents)  │
│     │                                          │       └─────────────────────────┘
│     │  JS in your browser calls YOUR laptop:   │
│     │                                          │
│     ├──► http://127.0.0.1:8766/ws/*   (files + git + Sylang symbols)
│     ├──► http://127.0.0.1:9001/       (agent alpha)
│     ├──► http://127.0.0.1:9002/       (agent beta)
│     └──► http://127.0.0.1:9003/       (agent gamma)
│                                                │
│   All four are one parent process:             │
│       `hermes-adapter up`                      │
│         ├── workspace API   (inline)            │
│         ├── hermes-a2a alpha   (child proc)    │
│         ├── hermes-a2a beta    (child proc)    │
│         └── hermes-a2a gamma   (child proc)    │
└───────────────────────────────────────────────┘
```

`hermes-adapter up` starts and supervises everything. `Ctrl-C` stops it cleanly.

---

## Step 1 — Run the installer (one command)

```bash
curl -fsSL https://raw.githubusercontent.com/balaji-embedcentrum/hermes-adapter/main/scripts/install.sh | bash
```

What it does:

- Finds Python 3.11+ on your system (tells you to install one if it can't).
- Creates an isolated venv at `~/.hermes-venv`.
- Installs `hermes-agent` + `hermes-adapter` into that venv from GitHub.
- Runs `hermes-adapter init` to scaffold `~/.hermes-adapter/agents.yaml` with a fresh bearer token and `https://hermes-studio.com` allowed as a CORS origin.

When it's done, activate the venv in any shell where you want to use the commands:

```bash
source ~/.hermes-venv/bin/activate
```

## Step 2 — Add each agent you want

One command per agent. The adapter infers the provider env var from the model name, auto-picks a port, and creates the per-agent `HERMES_HOME` folder with `.env` + `config.yaml`.

```bash
# --- Claude Sonnet for code review ---
hermes-adapter agent add alpha \
    --model anthropic/claude-sonnet-4.6 \
    --description "Code review" \
    --prompt-key           # will prompt for ANTHROPIC_API_KEY, hidden input

# --- OpenRouter / Llama for fast triage ---
hermes-adapter agent add beta \
    --model openrouter/meta-llama/llama-3.1-70b-instruct \
    --description "Fast triage" \
    --prompt-key

# --- Local Llama via Ollama for offline work ---
hermes-adapter agent add gamma \
    --model openai/llama3.1 \
    --description "Offline Llama" \
    --base-url http://localhost:11434/v1 \
    --key dummy
```

Check what you've got:

```bash
hermes-adapter agent list
# NAME    PORT    MODEL                                           DESCRIPTION
# alpha   9001    anthropic/claude-sonnet-4.6                     Code review
# beta    9002    openrouter/meta-llama/llama-3.1-70b-instruct    Fast triage
# gamma   9003    openai/llama3.1                                 Offline Llama
```

## Step 3 — Start everything

```bash
hermes-adapter up
```

You'll see:

```
workspace API: http://127.0.0.1:8766  (workspace=/Users/you/hermes-workspaces)
agent 'alpha' started (pid=44021, port=9001, log=~/.hermes-adapter/logs/alpha.log)
agent 'beta'  started (pid=44022, port=9002, log=~/.hermes-adapter/logs/beta.log)
agent 'gamma' started (pid=44023, port=9003, log=~/.hermes-adapter/logs/gamma.log)
```

Leave this terminal open. Ctrl-C → graceful shutdown of everything at once.

Want it in the background?

```bash
hermes-adapter up --detach
# ✓ supervisor detached (pid=44020)

hermes-adapter status
# supervisor pid=44020  alive
#   adapter:  http://127.0.0.1:8766
#   agent alpha  pid=44021  port=9001  alive
#   agent beta   pid=44022  port=9002  alive
#   agent gamma  pid=44023  port=9003  alive

hermes-adapter down
# ✓ supervisor (pid=44020) stopped
```

## Step 4 — Plug into hermes-studio.com

Grab the bearer token:

```bash
grep '^a2a_key' ~/.hermes-adapter/agents.yaml
# a2a_key: <long-random-string>
```

Open `https://hermes-studio.com`, sign in, and in **Settings → My agents** paste:

| Field | Value |
|---|---|
| Adapter URL | `http://127.0.0.1:8766` |
| A2A bearer | *(the token above)* |
| Agents | `alpha=http://127.0.0.1:9001`, `beta=http://127.0.0.1:9002`, `gamma=http://127.0.0.1:9003` |

Save. Studio's JS calls your laptop directly — nothing about your agents or files traverses Studio's server.

### Verify it works from the browser

DevTools → Console on any page:

```js
fetch("http://127.0.0.1:8766/ws", {
  headers: { Authorization: "Bearer <paste-your-a2a-key>" }
}).then(r => r.json()).then(console.log);
```

Expected: `{status: "ok", workspaces: [...]}`. If you get a CORS error, check that `hermes-adapter agent list`'s config has `https://hermes-studio.com` in the allowed origins (`hermes-adapter init --force --cors-origins https://hermes-studio.com,https://your-other-origin.com` to reset).

---

## Everyday commands

| Task | Command |
|---|---|
| Start everything | `hermes-adapter up` (Ctrl-C to stop) |
| Start in background | `hermes-adapter up --detach` |
| Stop detached stack | `hermes-adapter down` |
| See what's running | `hermes-adapter status` |
| List configured agents | `hermes-adapter agent list` |
| Add another agent | `hermes-adapter agent add <name> --model <model> --prompt-key` |
| Remove an agent | `hermes-adapter agent remove <name>` (add `--purge` to delete files) |
| Tail one agent's logs | `tail -f ~/.hermes-adapter/logs/alpha.log` |
| Change alpha's model | Edit `~/.hermes-adapter/agents/alpha/config.yaml`, then `hermes-adapter down && hermes-adapter up` |
| Change alpha's key | Edit `~/.hermes-adapter/agents/alpha/.env`, then `down && up` |

---

## File layout on your machine

```
~/.hermes-venv/                    Python venv with hermes-agent + hermes-adapter
~/hermes-workspaces/               all repos every agent can read/write
~/.hermes-adapter/
    agents.yaml                    the one declarative config (chmod 600)
    agents/
        alpha/                     HERMES_HOME for alpha
            .env                   ANTHROPIC_API_KEY=...
            config.yaml            model.default: anthropic/claude-sonnet-4.6
        beta/
            .env                   OPENROUTER_API_KEY=...
            config.yaml            model.default: openrouter/meta-llama/llama-3.1-70b-instruct
        gamma/
            .env                   OPENAI_API_KEY=dummy, OPENAI_BASE_URL=...
            config.yaml            model.default: openai/llama3.1
    logs/
        alpha.log, beta.log, gamma.log, supervisor.log
    run/
        supervisor.pid, supervisor.json
```

Per-agent isolation comes for free: alpha's Anthropic key can't leak to beta, and removing beta touches nothing about alpha or gamma.

---

## Scaling up

**More agents?** Repeat `hermes-adapter agent add <name> ...`. Ports auto-increment from 9001. The supervisor scales fine to 10–20 on a typical laptop. Remember each `hermes-a2a` is a real Python process — budget ~250 MB RAM each idle.

**Agents elsewhere too?** Studio can mix sources in one session — keep your local stack running and also plug in:
- A remote single-user VPS → [deploy-user-vps.md](deploy-user-vps.md)
- A platform-managed fleet → [deploy-vps.md](deploy-vps.md)

See [agent-sources.md](agent-sources.md) for the overall mental model.

---

## Common failures

| Symptom | Fix |
|---|---|
| `hermes-adapter: command not found` | `source ~/.hermes-venv/bin/activate` |
| `a2a-sdk is not installed` | Re-run the installer, or `pip install 'hermes-adapter[a2a]'` inside the venv |
| Agent dies with auth error | Wrong provider key in that agent's `.env`. Fix and restart the stack. |
| Port already in use | Another process on 8766/9001/etc. Use `lsof -iTCP:8766` to find it, or `hermes-adapter init --force --adapter-port 18766` to move ports. |
| CORS error in the browser | `https://hermes-studio.com` (or whatever Studio's real origin is) is not in `~/.hermes-adapter/agents.yaml`'s `cors_origins`. Edit and restart. |
| Supervisor is stuck | `hermes-adapter down`. If that fails, `pkill -f hermes-a2a; pkill -f 'hermes-adapter up'`. |
