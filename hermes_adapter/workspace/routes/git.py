"""Git operations over HTTP — status / commit / push / pull / pr / log / files / diff / branches / show."""

from __future__ import annotations

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
            code = line[:2].strip()
            path = line[3:]
            changed.append({"path": path, "status": code[0] if code else "?"})

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
