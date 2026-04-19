# Where agents can live — the three-tier model

Every Hermes Studio user plugs in agents from exactly one (or a mix) of three places. All three use the **same** wire protocol — Studio's browser JS calls an Adapter URL + an A2A URL, with a bearer token, plus CORS. The only thing that changes between them is who owns the infrastructure.

```
                     Hermes Studio (hermes-studio.com)
                                   │
               ┌───────────────────┼────────────────────────────┐
               │                   │                            │
               ▼                   ▼                            ▼
      (1) User's laptop    (2) User's own VPS          (3) Platform-hosted
      ┌──────────────┐     ┌──────────────────┐        ┌──────────────────┐
      │  adapter     │     │  adapter         │        │  adapter(s)      │
      │  127.0.0.1   │     │  alice.example   │        │  agents.hermes-  │
      │  :8766       │     │  /ws/*           │        │  studio.com/*    │
      │              │     │                  │        │                  │
      │  5 local     │     │  3 agents on     │        │  N agents in     │
      │  agents      │     │  her VPS         │        │  your fleet      │
      └──────────────┘     └──────────────────┘        └──────────────────┘
         Owner: user          Owner: user                Owner: platform
         TLS:   n/a           TLS:   user's cert         TLS:   your cert
         Reach: browser only  Reach: public internet     Reach: public internet
         Cost:  $0            Cost:  user pays VPS       Cost:  platform pays
```

## What Studio needs to store per user

For every agent source a user wires up, save:

| Field | Example (tier 1) | Example (tier 2) | Example (tier 3) |
|---|---|---|---|
| Adapter URL | `http://127.0.0.1:8766` | `https://alice.example/ws-api` | `https://agents.hermes-studio.com/t/{tenant}` |
| A2A bearer token | `local-dev-key` | user-generated secret | platform-generated, scoped to tenant |
| Agents | `alpha=http://127.0.0.1:9001` | `alpha=https://alice.example/a2a/alpha` | `alpha=https://agents.hermes-studio.com/t/{tenant}/a2a/alpha` |

Studio persists these per user (database row or encrypted browser storage — your call). The browser reads them and issues `fetch()` calls directly to those URLs.

## What each tier requires from the operator

### Tier 1 — user's laptop
- User installs `hermes-adapter` + `hermes-agent`
- Runs with `HERMES_ADAPTER_CORS_ORIGINS=https://hermes-studio.com`
- That's it
- Guide: [deploy-local.md](deploy-local.md)

### Tier 2 — user's own VPS
- User provisions any VPS (Hetzner, DigitalOcean, etc.)
- Installs the same `hermes-adapter` + `hermes-agent` but behind a reverse proxy with TLS
- Their adapter must accept CORS from `https://hermes-studio.com`
- Their A2A bearer is visible to Studio JS (and therefore to anyone who can read the user's browser memory — acceptable because the user is the only one using their own VPS)
- Guide: [deploy-user-vps.md](deploy-user-vps.md)

### Tier 3 — platform-hosted (you, the hermes-studio.com operator)
- You run the compose stack from [deploy-vps.md](deploy-vps.md)
- Multi-tenant: isolate users via `/ws/activate` + a per-tenant path prefix in Traefik (`/t/{tenant}/ws/*`, `/t/{tenant}/a2a/alpha/*`)
- You issue per-tenant bearer tokens; Studio injects them into each user's browser session
- Your auth layer must validate the user owns `{tenant}` before proxying
- Guide: [deploy-vps.md](deploy-vps.md)

## Why one mental model works for all three

The adapter and `hermes-a2a` have no concept of "local vs remote vs platform." They're just HTTP servers. Studio's client code doesn't know either — it reads three fields (URL, token, agent list) and issues `fetch()`. That's the design discipline that lets a single user seamlessly mix sources: one agent on their laptop, one on their VPS, one rented from you, all answering in the same Studio tab.

If you keep that invariant — **Adapter + Agent are plain HTTP servers, Studio is a thin HTTP client** — the three-tier model scales to a fourth and fifth tier for free (a colleague's VPS, an enterprise SSO-protected endpoint, a coworker's Tailnet node, …).
