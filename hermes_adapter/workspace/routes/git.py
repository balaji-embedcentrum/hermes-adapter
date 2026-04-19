"""Git operations over HTTP — status / commit / push / pull / pr / log / files."""

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
