"""Git operations over HTTP — full status/commit/diff/branch lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import json

from aiohttp import web

from .. import proc
from ..repo_finder import find_repo


async def _require_workspace(request: web.Request) -> tuple[str | None, web.Response | None]:
    repo = request.match_info["repo"]
    workspace = find_repo(repo)
    if not workspace:
        return None, web.json_response(
            {"status": "error", "message": f"Workspace for '{repo}' not found"}, status=404
        )
    return workspace, None


async def handle_status(request: web.Request) -> web.Response:
    workspace, err = await _require_workspace(request)
    if err:
        return err

    _, out, _ = await proc.run(["git", "status", "--porcelain"], workspace)  # type: ignore[arg-type]
    changed = []
    for line in out.splitlines():
        if len(line) >= 3:
            # Porcelain v1 format is "XY path" where X = index/staged status
            # and Y = worktree/unstaged status (each a single char, possibly
            # space). Keep both so the UI can segregate staged from unstaged.
            index = line[0]
            worktree = line[1]
            path = line[3:]
            changed.append({
                "path": path,
                "index": index,
                "worktree": worktree,
                # Convenience: a single "most interesting" letter for callers
                # that don't care about the split (worktree takes precedence,
                # since that's typically what the user sees first).
                "status": worktree if worktree != " " else index,
            })

    rc2, rev, _ = await proc.run(
        ["git", "rev-list", "--count", "--left-right", "@{u}...HEAD"], workspace  # type: ignore[arg-type]
    )
    ahead, behind = 0, 0
    if rc2 == 0 and "\t" in rev:
        b, a = rev.strip().split("\t")
        behind, ahead = int(b or 0), int(a or 0)

    return web.json_response({"status": "ok", "changed": changed, "ahead": ahead, "behind": behind})


async def handle_commit(request: web.Request) -> web.Response:
    workspace, err = await _require_workspace(request)
    if err:
        return err

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    message = (body.get("message") or "").strip()
    if not message:
        return web.json_response(
            {"status": "error", "message": "commit message required"}, status=400
        )

    # auto_stage defaults to True — preserves the long-standing behavior
    # of `git add -A && git commit`. Set False to commit only what is
    # already in the index (selective-staging UX).
    auto_stage = body.get("auto_stage", True)
    if auto_stage:
        await proc.run(["git", "add", "-A"], workspace)  # type: ignore[arg-type]
    rc, out, errout = await proc.run(["git", "commit", "-m", message], workspace)  # type: ignore[arg-type]
    if rc == 0:
        sha = ""
        for line in out.splitlines():
            if "]" in line:
                parts = line.split("]")
                sha = parts[0].split()[-1] if parts else ""
                break
        return web.json_response({"status": "ok", "sha": sha, "output": out.strip()})
    return web.json_response(
        {"status": "error", "message": errout.strip() or out.strip()}, status=500
    )


async def handle_push(request: web.Request) -> web.Response:
    workspace, err = await _require_workspace(request)
    if err:
        return err
    rc, out, errout = await proc.run(["git", "push", "origin", "HEAD"], workspace)  # type: ignore[arg-type]
    if rc == 0:
        return web.json_response({"status": "ok", "output": out.strip() or errout.strip()})
    return web.json_response({"status": "error", "message": errout.strip()}, status=500)


async def handle_pull(request: web.Request) -> web.Response:
    workspace, err = await _require_workspace(request)
    if err:
        return err
    rc, out, errout = await proc.run(
        ["git", "pull", "--rebase", "origin", "HEAD"], workspace  # type: ignore[arg-type]
    )
    if rc == 0:
        return web.json_response({"status": "ok", "output": out.strip()})
    return web.json_response({"status": "error", "message": errout.strip()}, status=500)


async def handle_pr(request: web.Request) -> web.Response:
    workspace, err = await _require_workspace(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    title = (body.get("title") or "").strip()
    pr_body = body.get("body", "")
    base = body.get("base", "main")
    if not title:
        return web.json_response({"status": "error", "message": "title required"}, status=400)

    cmd = ["gh", "pr", "create", "--title", title, "--body", pr_body, "--base", base]
    rc, out, errout = await proc.run(cmd, workspace)  # type: ignore[arg-type]
    if rc == 0:
        pr_url = out.strip().splitlines()[-1] if out.strip() else ""
        return web.json_response({"status": "ok", "pr_url": pr_url})
    return web.json_response({"status": "error", "message": errout.strip()}, status=500)


async def handle_log(request: web.Request) -> web.Response:
    workspace, err = await _require_workspace(request)
    if err:
        return err

    limit = int(request.rel_url.query.get("limit", "50"))
    # Unit Separator (0x1F) — non-printable, never appears in commit text, and
    # (unlike NUL) allowed in subprocess argv on all platforms / Python 3.12+.
    SEP = "\x1f"
    fmt = f"%H{SEP}%h{SEP}%an{SEP}%ae{SEP}%aI{SEP}%s{SEP}%P"
    rc, out, errout = await proc.run(
        ["git", "log", f"--format={fmt}", f"-n{limit}"], workspace  # type: ignore[arg-type]
    )
    if rc != 0:
        return web.json_response({"status": "error", "message": errout.strip()}, status=500)

    commits: list[dict] = []
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split(SEP, 6)
        if len(parts) >= 6:
            commits.append(
                {
                    "hash": parts[0],
                    "shortHash": parts[1],
                    "author": parts[2],
                    "email": parts[3],
                    "date": parts[4],
                    "message": parts[5],
                    "parents": parts[6].split() if len(parts) > 6 and parts[6] else [],
                }
            )
    return web.json_response({"status": "ok", "commits": commits})


async def handle_files(request: web.Request) -> web.Response:
    workspace, err = await _require_workspace(request)
    if err:
        return err
    commit_hash = request.rel_url.query.get("commit", "HEAD")
    rc, out, errout = await proc.run(
        ["git", "diff-tree", "--no-commit-id", "-r", "--name-status", commit_hash],
        workspace,  # type: ignore[arg-type]
    )
    if rc != 0:
        return web.json_response({"status": "error", "message": errout.strip()}, status=500)

    status_map = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
    files: list[dict] = []
    for line in out.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            files.append({"status": status_map.get(parts[0][0], parts[0]), "path": parts[-1]})
    return web.json_response({"status": "ok", "files": files})


async def handle_diff(request: web.Request) -> web.Response:
    """Unified diff for a path (or whole tree).

    Query params (all optional):
      path   — restrict diff to one pathspec
      staged — "true" to show `git diff --cached`
      ref    — SHA/ref to show that commit's patch vs its parent (works for root commits too)
    When neither ``staged`` nor ``ref`` is set, returns the unstaged working-tree diff.
    """
    workspace, err = await _require_workspace(request)
    if err:
        return err

    q = request.rel_url.query
    path = q.get("path", "").strip()
    staged = q.get("staged", "").lower() == "true"
    ref = q.get("ref", "").strip()

    if ref:
        # `git show --format=` strips the commit header and prints just the patch.
        # Works for root commits (unlike `<ref>^..<ref>`).
        args = ["git", "show", "--format=", ref]
    else:
        args = ["git", "diff"]
        if staged:
            args.append("--cached")
    if path:
        args.extend(["--", path])

    rc, out, errout = await proc.run(args, workspace)  # type: ignore[arg-type]
    if rc != 0:
        return web.json_response({"status": "error", "message": errout.strip()}, status=400)
    return web.json_response({"status": "ok", "diff": out})


async def handle_branches(request: web.Request) -> web.Response:
    """List local + remote branches and the current HEAD.

    Returns ``current`` as the branch name, or ``null`` when HEAD is detached.
    ``head_sha`` is always the current HEAD short SHA.
    """
    workspace, err = await _require_workspace(request)
    if err:
        return err

    rc_cur, cur_out, _ = await proc.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], workspace  # type: ignore[arg-type]
    )
    current: str | None = cur_out.strip() if rc_cur == 0 else None
    if current == "HEAD":
        current = None  # detached

    _, sha_out, _ = await proc.run(
        ["git", "rev-parse", "--short", "HEAD"], workspace  # type: ignore[arg-type]
    )
    head_sha = sha_out.strip()

    SEP = "\x1f"
    fmt = f"%(refname:short){SEP}%(objectname:short){SEP}%(upstream:short)"
    _, local_out, _ = await proc.run(
        ["git", "for-each-ref", f"--format={fmt}", "refs/heads"], workspace  # type: ignore[arg-type]
    )
    local: list[dict] = []
    for line in local_out.splitlines():
        if not line.strip():
            continue
        parts = line.split(SEP)
        entry: dict = {"name": parts[0], "sha": parts[1] if len(parts) > 1 else ""}
        if len(parts) > 2 and parts[2]:
            entry["upstream"] = parts[2]
        local.append(entry)

    _, remote_out, _ = await proc.run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/remotes"], workspace  # type: ignore[arg-type]
    )
    remote = [line.strip() for line in remote_out.splitlines()
              if line.strip() and not line.strip().endswith("/HEAD")]

    return web.json_response(
        {"status": "ok", "current": current, "head_sha": head_sha, "local": local, "remote": remote}
    )


async def handle_show(request: web.Request) -> web.Response:
    """Return commit metadata, changed-file list, and full patch for a ref."""
    workspace, err = await _require_workspace(request)
    if err:
        return err
    sha = request.match_info["sha"]

    SEP = "\x1f"
    fmt = f"%H{SEP}%h{SEP}%an{SEP}%ae{SEP}%aI{SEP}%s{SEP}%P"
    rc, meta_out, errout = await proc.run(
        ["git", "log", "-1", f"--format={fmt}", sha], workspace  # type: ignore[arg-type]
    )
    if rc != 0 or not meta_out.strip():
        return web.json_response(
            {"status": "error", "message": errout.strip() or f"unknown ref: {sha}"},
            status=404,
        )
    parts = meta_out.strip().split(SEP, 6)
    commit = {
        "hash": parts[0],
        "shortHash": parts[1],
        "author": parts[2],
        "email": parts[3],
        "date": parts[4],
        "message": parts[5],
        "parents": parts[6].split() if len(parts) > 6 and parts[6] else [],
    }

    _, files_out, _ = await proc.run(
        ["git", "diff-tree", "--no-commit-id", "--root", "-r", "--name-status", sha],
        workspace,  # type: ignore[arg-type]
    )
    status_map = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
    files: list[dict] = []
    for line in files_out.strip().splitlines():
        fparts = line.split("\t", 2)
        if len(fparts) >= 2:
            files.append({"status": status_map.get(fparts[0][0], fparts[0]), "path": fparts[-1]})

    _, diff_out, _ = await proc.run(
        ["git", "show", "--format=", sha], workspace  # type: ignore[arg-type]
    )

    return web.json_response({"status": "ok", "commit": commit, "files": files, "diff": diff_out})


async def _read_paths(request: web.Request) -> tuple[list[str] | None, web.Response | None]:
    try:
        body = await request.json()
    except Exception:
        return None, web.json_response(
            {"status": "error", "message": "Invalid JSON"}, status=400
        )
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        return None, web.json_response(
            {"status": "error", "message": "paths (non-empty list of strings) required"},
            status=400,
        )
    return paths, None


async def handle_stage(request: web.Request) -> web.Response:
    """Stage one or more paths. Body: ``{ paths: [str, ...] }``."""
    workspace, err = await _require_workspace(request)
    if err:
        return err
    paths, perr = await _read_paths(request)
    if perr:
        return perr
    rc, _, errout = await proc.run(
        ["git", "add", "--", *paths],  # type: ignore[list-item]
        workspace,  # type: ignore[arg-type]
    )
    if rc != 0:
        return web.json_response({"status": "error", "message": errout.strip()}, status=400)
    return web.json_response({"status": "ok", "staged": paths})


async def handle_unstage(request: web.Request) -> web.Response:
    """Remove one or more paths from the index. Body: ``{ paths: [str, ...] }``."""
    workspace, err = await _require_workspace(request)
    if err:
        return err
    paths, perr = await _read_paths(request)
    if perr:
        return perr
    # `reset HEAD --` works on repos with at least one commit; _require_workspace
    # already rejects non-repos. On a repo with zero commits, git will error,
    # which we surface.
    rc, _, errout = await proc.run(
        ["git", "reset", "HEAD", "--", *paths],  # type: ignore[list-item]
        workspace,  # type: ignore[arg-type]
    )
    if rc != 0:
        return web.json_response({"status": "error", "message": errout.strip()}, status=400)
    return web.json_response({"status": "ok", "unstaged": paths})


async def handle_discard(request: web.Request) -> web.Response:
    """Discard unstaged working-tree changes for the given paths.

    This does NOT remove untracked files (use a file DELETE for that) and does
    NOT unstage already-staged changes (call ``/unstage`` first, then this).
    """
    workspace, err = await _require_workspace(request)
    if err:
        return err
    paths, perr = await _read_paths(request)
    if perr:
        return perr
    rc, _, errout = await proc.run(
        ["git", "checkout", "--", *paths],  # type: ignore[list-item]
        workspace,  # type: ignore[arg-type]
    )
    if rc != 0:
        return web.json_response({"status": "error", "message": errout.strip()}, status=400)
    return web.json_response({"status": "ok", "discarded": paths})


async def handle_checkout(request: web.Request) -> web.Response:
    """Switch branches. Body: ``{ branch: str, create?: bool }``.

    When ``create`` is true, this is equivalent to ``git checkout -b <branch>``.
    """
    workspace, err = await _require_workspace(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    branch = (body.get("branch") or "").strip()
    if not branch:
        return web.json_response(
            {"status": "error", "message": "branch required"}, status=400
        )
    create = bool(body.get("create", False))
    args = ["git", "checkout"]
    if create:
        args.append("-b")
    args.append(branch)
    rc, _, errout = await proc.run(args, workspace)  # type: ignore[arg-type]
    if rc != 0:
        return web.json_response({"status": "error", "message": errout.strip()}, status=400)
    return web.json_response({"status": "ok", "branch": branch, "created": create})


async def handle_branch(request: web.Request) -> web.Response:
    """Create a new branch without switching to it. Body: ``{ name: str, from?: str }``."""
    workspace, err = await _require_workspace(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    name = (body.get("name") or "").strip()
    if not name:
        return web.json_response(
            {"status": "error", "message": "name required"}, status=400
        )
    from_ref = (body.get("from") or "").strip()
    args = ["git", "branch", name]
    if from_ref:
        args.append(from_ref)
    rc, _, errout = await proc.run(args, workspace)  # type: ignore[arg-type]
    if rc != 0:
        return web.json_response({"status": "error", "message": errout.strip()}, status=400)
    return web.json_response({"status": "ok", "name": name, "from": from_ref or None})


async def handle_events(request: web.Request) -> web.StreamResponse:
    """Server-Sent Events stream: emits `git.status.changed` on repo change.

    Implementation: the handler polls `git status --porcelain` once per second
    and pushes an event every time the output hash shifts. Clients can reuse
    this to replace client-side polling — same latency, single connection.

    No filesystem watcher (watchdog) — polling the index is lightweight
    (git itself caches stat info) and avoids an extra runtime dep. Connection
    closes cleanly on client disconnect via asyncio cancellation.
    """
    workspace, err = await _require_workspace(request)
    if err:
        return err

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            # CORS handled by the global cors_middleware; this just unblocks
            # EventSource for direct same-origin-or-allowed-origin calls.
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)

    last_hash: str | None = None
    try:
        # Emit an immediate "connected" comment so the browser sees bytes
        # before waiting for the first real change.
        await resp.write(b": connected\n\n")
        while True:
            _, out, _ = await proc.run(
                ["git", "status", "--porcelain"], workspace  # type: ignore[arg-type]
            )
            h = hashlib.sha1(out.encode()).hexdigest()
            if h != last_hash:
                last_hash = h
                payload = json.dumps({"hash": h})
                await resp.write(
                    f"event: git.status.changed\ndata: {payload}\n\n".encode()
                )
            await asyncio.sleep(1.0)
    except (asyncio.CancelledError, ConnectionResetError):
        # Client closed — nothing to do, let the response tear down naturally.
        pass
    return resp


async def handle_blob(request: web.Request) -> web.Response:
    """Return file content at a specific ref. Query: ``path=<rel>&ref=<ref>``.

    Used by the Studio diff view to fetch the HEAD (or any ref's) version of
    a file for side-by-side comparison against the working tree.
    """
    workspace, err = await _require_workspace(request)
    if err:
        return err
    q = request.rel_url.query
    path = q.get("path", "").strip()
    ref = q.get("ref", "HEAD").strip() or "HEAD"
    if not path:
        return web.json_response(
            {"status": "error", "message": "path required"}, status=400
        )
    rc, out, errout = await proc.run(
        ["git", "show", f"{ref}:{path}"], workspace  # type: ignore[arg-type]
    )
    if rc != 0:
        # Common case: file didn't exist at that ref (new file in working tree).
        # Return empty content with 200 so the diff view can show "all additions".
        if "does not exist" in errout or "exists on disk, but not in" in errout:
            return web.json_response({"status": "ok", "path": path, "ref": ref, "content": ""})
        return web.json_response(
            {"status": "error", "message": errout.strip()}, status=404
        )
    return web.json_response({"status": "ok", "path": path, "ref": ref, "content": out})


async def handle_fetch(request: web.Request) -> web.Response:
    """Fetch all remotes with pruning. Runs ``git fetch --all --prune``."""
    workspace, err = await _require_workspace(request)
    if err:
        return err
    rc, out, errout = await proc.run(
        ["git", "fetch", "--all", "--prune"], workspace  # type: ignore[arg-type]
    )
    if rc != 0:
        return web.json_response({"status": "error", "message": errout.strip()}, status=500)
    return web.json_response({"status": "ok", "output": (out or errout).strip()})
