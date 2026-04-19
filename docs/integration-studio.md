# Integration: Hermes Studio

Hermes Studio is a browser IDE that needs file-tree, editor, git panel, and Sylang symbol support. All of that comes from `hermes-adapter`'s workspace API — no LLM on the critical path.

## Topology

```
browser ─HTTPS→ Studio web app ──────┐
                                      ├─→ :8766  hermes-adapter (workspace + A2A)
                                      └─→ :8765  hermes-agent  (chat)
```

## 1. Run the adapter

```bash
export HERMES_WORKSPACE_DIR=/var/studio/workspaces
hermes-adapter-workspace --host 127.0.0.1 --port 8766
```

Or via Docker:

```bash
docker compose up adapter
```

## 2. Studio env

```env
# .env.local for the Studio Next.js app
HERMES_ADAPTER_URL=http://127.0.0.1:8766
HERMES_AGENT_URL=http://127.0.0.1:8765
```

## 3. Typed client (TypeScript)

```ts
export class WorkspaceClient {
  constructor(private readonly base = process.env.HERMES_ADAPTER_URL!) {}

  async list() {
    return fetch(`${this.base}/ws`).then(r => r.json());
  }
  async tree(repo: string, path = "") {
    return fetch(`${this.base}/ws/${repo}/tree?path=${encodeURIComponent(path)}`).then(r => r.json());
  }
  async readFile(repo: string, path: string) {
    return fetch(`${this.base}/ws/${repo}/file?path=${encodeURIComponent(path)}`).then(r => r.json());
  }
  async writeFile(repo: string, path: string, content: string) {
    return fetch(`${this.base}/ws/${repo}/file`, {
      method: "POST", headers: {"content-type": "application/json"},
      body: JSON.stringify({ path, content }),
    }).then(r => r.json());
  }
  async symbols(repo: string) {
    return fetch(`${this.base}/ws/${repo}/symbols`).then(r => r.json());
  }
  async status(repo: string) {
    return fetch(`${this.base}/ws/${repo}/git/status`).then(r => r.json());
  }
  async commit(repo: string, message: string) {
    return fetch(`${this.base}/ws/${repo}/git/commit`, {
      method: "POST", headers: {"content-type": "application/json"},
      body: JSON.stringify({ message }),
    }).then(r => r.json());
  }
}
```

## 4. Per-user isolation

Point the adapter at a symlinked root and flip the symlink on login:

```env
HERMES_WORKSPACE_DIR=/var/studio/mount/active
```

```ts
await fetch(`${base}/ws/activate`, {
  method: "POST", headers: {"content-type": "application/json"},
  body: JSON.stringify({ user: session.user.githubLogin }),
});
```

Studio's auth middleware calls `/ws/activate` on session resume and `/ws/deactivate` on logout. The adapter refuses nested scans when the root ends in `/active`, so users cannot see each other's repos even if they guess `{repo}` names.

## 5. Reverse proxy

Don't expose `:8766` directly. Front it with Studio's own Next.js API routes:

```ts
// app/api/ws/[...path]/route.ts — thin pass-through that checks auth first
export async function GET(req: Request, { params }: { params: { path: string[] } }) {
  const session = await requireSession(req);
  await activateWorkspace(session.user.githubLogin);          // POST /ws/activate
  return fetch(`${ADAPTER}/ws/${params.path.join("/")}`);
}
```
