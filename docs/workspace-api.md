# Workspace API

Plain HTTP routes over filesystem + git. No LLM, no agent.

Base URL: `http://{HERMES_ADAPTER_HOST}:{HERMES_ADAPTER_PORT}` (default `:8766`).

All routes that mutate state invalidate the Sylang symbols cache for the affected repo, so the next `GET /ws/{repo}/symbols` is fresh.

## Repo resolution

`{repo}` is looked up against `HERMES_WORKSPACE_DIR` (default `/workspaces`) in this order:

1. `login/repo` — direct user-scoped path (also tries the parent of `/active` when the root is symlink-isolated).
2. `{root}/{repo}` — flat layout or active-symlink isolation.
3. `{root}/{any_user}/{repo}` — nested multi-user layout; skipped when root ends in `/active`.
4. Legacy fallbacks: `/root/{repo}`, `~/{repo}`, etc.

## Workspaces

### `GET /ws`
Lists every workspace directory discoverable under the root. Returns both flat (`{root}/{repo}`) and nested (`{root}/{user}/{repo}`) layouts.

```json
{ "status": "ok", "root": "/workspaces",
  "workspaces": [
    {"name": "balaji/hermes-adapter", "path": "balaji/hermes-adapter", "abs_path": "/workspaces/balaji/hermes-adapter"}
  ]
}
```

### `POST /ws/activate`
Symlinks `{mount}/active` → `{mount}/{user}`. Body: `{"user": "githubLogin"}`.

### `POST /ws/deactivate`
Removes the `active` symlink.

## Repo lifecycle

### `POST /ws/{repo}/init`
Clone or pull. If the repo exists it runs `git pull --rebase`. Otherwise:
- `{"url": "...", "branch": "main"}` → clones into `{root}/{githubOwner}/{repo}`.
- `{"empty": true}` → creates a new empty git repo with a `.gitkeep`.

## Files

### `GET /ws/{repo}/tree?path=`
Single-level directory listing. `path` is relative to workspace root.

### `GET /ws/{repo}/file?path=`
Returns `{ "content": "<utf-8 text>" }`. Rejects path traversal.

### `POST /ws/{repo}/file`
Writes a file. Body: `{"path": "docs/README.md", "content": "…"}`. Creates intermediate dirs.

### `DELETE /ws/{repo}/file?path=`
Deletes a file or directory (`rmtree` for directories).

## Git

| Route | Body / Query | Returns |
|-------|--------------|---------|
| `GET /ws/{repo}/git/status` | — | `{changed, ahead, behind}` |
| `POST /ws/{repo}/git/commit` | `{message}` | `{sha, output}` |
| `POST /ws/{repo}/git/push` | — | `{output}` |
| `POST /ws/{repo}/git/pull` | — | `{output}` |
| `POST /ws/{repo}/git/pr` | `{title, body?, base?}` | `{pr_url}` (uses `gh`) |
| `GET /ws/{repo}/git/log?limit=50` | — | `{commits: [{hash, author, date, message, parents}]}` |
| `GET /ws/{repo}/git/files?commit=` | — | `{files: [{status, path}]}` |

## Sylang symbols

### `GET /ws/{repo}/symbols`
Batch-reads every Sylang file in the workspace and returns them in one response. Cached in-process for 5 minutes; cache is invalidated whenever a file is written or deleted via this API.

Recognized extensions: `.req .agt .blk .fml .fun .haz .ifc .itm .ple .sam .seq .sgl .smd .spec .spr .tst .ucd .vcf .vml .fta .flr .dash`

Ignored directories: `.git`, `node_modules`, `.next`, `dist`, `.turbo`, `.cache`, `__pycache__`.

```json
{ "files": [{"path": "models/item.itm", "content": "..."}], "fileCount": 42, "cachedAt": 1712345678901 }
```

### `POST /ws/{repo}/symbols/invalidate`
Force-clears the cache for `{repo}`.
