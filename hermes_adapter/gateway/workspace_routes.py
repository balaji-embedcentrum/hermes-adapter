"""Workspace filesystem + git + Sylang symbols, ported to Starlette.

Mirrors the aiohttp handlers under ``hermes_adapter/workspace/routes/``.
The underlying helper modules (``repo_finder``, ``proc``, ``symbols_cache``)
are framework-agnostic and reused as-is.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil as _shutil

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..workspace import proc, symbols_cache
from ..workspace.repo_finder import find_repo, resolve_safe_path, workspace_root

logger = logging.getLogger(__name__)


def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"status": "error", "message": msg}, status_code=status)


def _ok(payload: dict) -> JSONResponse:
    return JSONResponse({"status": "ok", **payload})


# ---------------------------------------------------------------------------
# /ws — list workspaces
# ---------------------------------------------------------------------------

async def handle_list(request: Request) -> JSONResponse:
    root = workspace_root()
    workspaces: list[dict] = []
    if os.path.isdir(root):
        try:
            for entry in sorted(os.scandir(root), key=lambda e: e.name.lower()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if os.path.isdir(os.path.join(entry.path, ".git")):
                    workspaces.append(
                        {"name": entry.name, "path": entry.name, "abs_path": entry.path}
                    )
                else:
                    try:
                        for child in sorted(os.scandir(entry.path), key=lambda e: e.name.lower()):
                            if not child.is_dir() or child.name.startswith("."):
                                continue
                            if os.path.isdir(os.path.join(child.path, ".git")):
                                workspaces.append(
                                    {
                                        "name": f"{entry.name}/{child.name}",
                                        "path": os.path.relpath(child.path, root),
                                        "abs_path": child.path,
                                    }
                                )
                    except PermissionError:
                        pass
        except PermissionError:
            pass
    return JSONResponse({"status": "ok", "workspaces": workspaces, "root": root})


# ---------------------------------------------------------------------------
# /ws/activate | /ws/deactivate
# ---------------------------------------------------------------------------

def _active_paths():
    root = workspace_root()
    mount = os.path.dirname(root) if root.rstrip("/").endswith("/active") else root
    return mount, os.path.join(mount, "active")


async def handle_activate(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    user = (body.get("user") or "").strip()
    if not user or "/" in user or ".." in user:
        return _err("Invalid user")

    mount, active_link = _active_paths()
    user_dir = os.path.join(mount, user)
    os.makedirs(user_dir, exist_ok=True)
    try:
        if os.path.islink(active_link):
            os.unlink(active_link)
        elif os.path.isdir(active_link):
            _shutil.rmtree(active_link, ignore_errors=True)
        elif os.path.exists(active_link):
            os.unlink(active_link)
    except OSError:
        pass
    os.symlink(user_dir, active_link)
    return _ok({"user": user, "active": active_link, "target": user_dir})


async def handle_deactivate(request: Request) -> JSONResponse:
    _, active_link = _active_paths()
    try:
        if os.path.islink(active_link):
            os.unlink(active_link)
    except OSError:
        pass
    return _ok({})


# ---------------------------------------------------------------------------
# /ws/{repo}/init
# ---------------------------------------------------------------------------

async def handle_init(request: Request) -> JSONResponse:
    repo = request.path_params["repo"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    url = body.get("url", "")
    branch = body.get("branch", "main")

    existing = find_repo(repo)
    if existing:
        rc, out, err = await proc.run(["git", "pull", "--rebase", "origin", branch], existing)
        return JSONResponse(
            {
                "status": "ok",
                "action": "pulled",
                "path": existing,
                "output": out.strip(),
                "error": err.strip() if rc else None,
            }
        )

    is_empty = bool(body.get("empty", False))
    if not url and not is_empty:
        return _err("Repo not found and no clone URL provided", 404)

    root = workspace_root()
    if is_empty:
        user_dir = None
        if os.path.isdir(root):
            for entry in sorted(os.scandir(root), key=lambda e: e.name):
                if entry.is_dir() and entry.name not in (".git", "local"):
                    user_dir = entry.path
                    break
        if not user_dir:
            user_dir = f"{root}/user"
        dest = f"{user_dir}/{repo}"
        os.makedirs(dest, exist_ok=True)
        await proc.run(["git", "init"], dest)
        gitkeep = os.path.join(dest, ".gitkeep")
        if not os.path.exists(gitkeep):
            with open(gitkeep, "w"):
                pass
        return _ok({"action": "created", "path": dest})

    owner = "user"
    if "github.com/" in url:
        parts = url.split("github.com/")[-1].split("/")
        if parts:
            owner = parts[0]
    dest_dir = f"{root}/{owner}"
    os.makedirs(dest_dir, exist_ok=True)
    dest = f"{dest_dir}/{repo}"
    rc, out, err = await proc.run(["git", "clone", "--branch", branch, url, dest], dest_dir)
    if rc == 0:
        return _ok({"action": "cloned", "path": dest})
    return _err(err.strip(), 500)


# ---------------------------------------------------------------------------
# /ws/{repo}/tree
# ---------------------------------------------------------------------------

async def handle_tree(request: Request) -> JSONResponse:
    repo = request.path_params["repo"]
    rel = request.query_params.get("path", "")
    ws = find_repo(repo)
    if not ws:
        return _err(f"Workspace for '{repo}' not found", 404)
    target = os.path.join(ws, rel) if rel else ws
    if not os.path.isdir(target):
        return _err("Path is not a directory")
    entries: list[dict] = []
    try:
        for entry in sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name.startswith(".git") and entry.name != ".gitignore":
                continue
            entries.append(
                {
                    "name": entry.name,
                    "path": os.path.relpath(entry.path, ws),
                    "type": "dir" if entry.is_dir() else "file",
                }
            )
    except PermissionError as e:
        return _err(str(e), 403)
    return JSONResponse({"status": "ok", "path": rel, "entries": entries})


# ---------------------------------------------------------------------------
# /ws/{repo}/file  — GET / POST / DELETE
# ---------------------------------------------------------------------------

async def handle_file_get(request: Request) -> JSONResponse:
    repo = request.path_params["repo"]
    rel = request.query_params.get("path", "")
    if not rel:
        return _err("path query param required")
    ws = find_repo(repo)
    if not ws:
        return _err(f"Workspace for '{repo}' not found", 404)
    abs_path = resolve_safe_path(ws, rel)
    if abs_path is None:
        return _err("Path traversal not allowed", 403)
    if not os.path.isfile(abs_path):
        return _err("File not found", 404)
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            return JSONResponse({"status": "ok", "path": rel, "content": f.read()})
    except Exception as e:
        return _err(str(e), 500)


async def handle_file_post(request: Request) -> JSONResponse:
    repo = request.path_params["repo"]
    ws = find_repo(repo)
    if not ws:
        return _err(f"Workspace for '{repo}' not found", 404)
    try:
        body = await request.json()
    except Exception:
        return _err("Invalid JSON body")
    rel = body.get("path", "")
    content = body.get("content", "")
    if not rel:
        return _err("path required")
    abs_path = resolve_safe_path(ws, rel)
    if abs_path is None:
        return _err("Path traversal not allowed", 403)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        symbols_cache.invalidate(repo)
        return JSONResponse({"status": "ok", "path": rel, "written": True})
    except Exception as e:
        return _err(str(e), 500)


async def handle_file_delete(request: Request) -> JSONResponse:
    repo = request.path_params["repo"]
    ws = find_repo(repo)
    if not ws:
        return _err(f"Workspace for '{repo}' not found", 404)
    rel = request.query_params.get("path", "")
    if not rel:
        return _err("path required")
    abs_path = resolve_safe_path(ws, rel)
    if abs_path is None:
        return _err("Path traversal not allowed", 403)
    try:
        if os.path.isfile(abs_path):
            os.remove(abs_path)
        elif os.path.isdir(abs_path):
            _shutil.rmtree(abs_path)
        else:
            return _err("File not found", 404)
        symbols_cache.invalidate(repo)
        return JSONResponse({"status": "ok", "path": rel, "deleted": True})
    except Exception as e:
        return _err(str(e), 500)


# ---------------------------------------------------------------------------
# /ws/{repo}/git/*
# ---------------------------------------------------------------------------

async def _require_ws(request: Request) -> tuple[str | None, JSONResponse | None]:
    repo = request.path_params["repo"]
    ws = find_repo(repo)
    if not ws:
        return None, _err(f"Workspace for '{repo}' not found", 404)
    return ws, None


async def handle_git_status(request: Request) -> JSONResponse:
    ws, err = await _require_ws(request)
    if err:
        return err
    _, out, _ = await proc.run(["git", "status", "--porcelain"], ws)  # type: ignore[arg-type]
    changed = []
    for line in out.splitlines():
        if len(line) >= 3:
            code = line[:2].strip()
            changed.append({"path": line[3:], "status": code[0] if code else "?"})
    rc, rev, _ = await proc.run(
        ["git", "rev-list", "--count", "--left-right", "@{u}...HEAD"], ws  # type: ignore[arg-type]
    )
    ahead, behind = 0, 0
    if rc == 0 and "\t" in rev:
        b, a = rev.strip().split("\t")
        behind, ahead = int(b or 0), int(a or 0)
    return JSONResponse({"status": "ok", "changed": changed, "ahead": ahead, "behind": behind})


async def handle_git_commit(request: Request) -> JSONResponse:
    ws, err = await _require_ws(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _err("Invalid JSON")
    msg = (body.get("message") or "").strip()
    if not msg:
        return _err("commit message required")
    await proc.run(["git", "add", "-A"], ws)  # type: ignore[arg-type]
    rc, out, errout = await proc.run(["git", "commit", "-m", msg], ws)  # type: ignore[arg-type]
    if rc == 0:
        sha = ""
        for line in out.splitlines():
            if "]" in line:
                sha = line.split("]")[0].split()[-1]
                break
        return JSONResponse({"status": "ok", "sha": sha, "output": out.strip()})
    return _err(errout.strip() or out.strip(), 500)


async def handle_git_push(request: Request) -> JSONResponse:
    ws, err = await _require_ws(request)
    if err:
        return err
    rc, out, errout = await proc.run(["git", "push", "origin", "HEAD"], ws)  # type: ignore[arg-type]
    if rc == 0:
        return JSONResponse({"status": "ok", "output": out.strip() or errout.strip()})
    return _err(errout.strip(), 500)


async def handle_git_pull(request: Request) -> JSONResponse:
    ws, err = await _require_ws(request)
    if err:
        return err
    rc, out, errout = await proc.run(["git", "pull", "--rebase", "origin", "HEAD"], ws)  # type: ignore[arg-type]
    if rc == 0:
        return JSONResponse({"status": "ok", "output": out.strip()})
    return _err(errout.strip(), 500)


async def handle_git_pr(request: Request) -> JSONResponse:
    ws, err = await _require_ws(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _err("Invalid JSON")
    title = (body.get("title") or "").strip()
    if not title:
        return _err("title required")
    cmd = ["gh", "pr", "create", "--title", title, "--body", body.get("body", ""),
           "--base", body.get("base", "main")]
    rc, out, errout = await proc.run(cmd, ws)  # type: ignore[arg-type]
    if rc == 0:
        pr_url = out.strip().splitlines()[-1] if out.strip() else ""
        return JSONResponse({"status": "ok", "pr_url": pr_url})
    return _err(errout.strip(), 500)


async def handle_git_log(request: Request) -> JSONResponse:
    ws, err = await _require_ws(request)
    if err:
        return err
    limit = int(request.query_params.get("limit", "50"))
    SEP = "\x1f"
    fmt = f"%H{SEP}%h{SEP}%an{SEP}%ae{SEP}%aI{SEP}%s{SEP}%P"
    rc, out, errout = await proc.run(["git", "log", f"--format={fmt}", f"-n{limit}"], ws)  # type: ignore[arg-type]
    if rc != 0:
        return _err(errout.strip(), 500)
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
    return JSONResponse({"status": "ok", "commits": commits})


async def handle_git_files(request: Request) -> JSONResponse:
    ws, err = await _require_ws(request)
    if err:
        return err
    commit_hash = request.query_params.get("commit", "HEAD")
    rc, out, errout = await proc.run(
        ["git", "diff-tree", "--no-commit-id", "-r", "--name-status", commit_hash], ws  # type: ignore[arg-type]
    )
    if rc != 0:
        return _err(errout.strip(), 500)
    status_map = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
    files: list[dict] = []
    for line in out.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            files.append({"status": status_map.get(parts[0][0], parts[0]), "path": parts[-1]})
    return JSONResponse({"status": "ok", "files": files})


async def handle_git_diff(request: Request) -> JSONResponse:
    ws, err = await _require_ws(request)
    if err:
        return err
    q = request.query_params
    path = (q.get("path") or "").strip()
    staged = (q.get("staged") or "").lower() == "true"
    ref = (q.get("ref") or "").strip()

    if ref:
        args = ["git", "show", "--format=", ref]
    else:
        args = ["git", "diff"]
        if staged:
            args.append("--cached")
    if path:
        args.extend(["--", path])

    rc, out, errout = await proc.run(args, ws)  # type: ignore[arg-type]
    if rc != 0:
        return _err(errout.strip(), 400)
    return JSONResponse({"status": "ok", "diff": out})


async def handle_git_branches(request: Request) -> JSONResponse:
    ws, err = await _require_ws(request)
    if err:
        return err

    rc_cur, cur_out, _ = await proc.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], ws  # type: ignore[arg-type]
    )
    current: str | None = cur_out.strip() if rc_cur == 0 else None
    if current == "HEAD":
        current = None

    _, sha_out, _ = await proc.run(
        ["git", "rev-parse", "--short", "HEAD"], ws  # type: ignore[arg-type]
    )
    head_sha = sha_out.strip()

    SEP = "\x1f"
    fmt = f"%(refname:short){SEP}%(objectname:short){SEP}%(upstream:short)"
    _, local_out, _ = await proc.run(
        ["git", "for-each-ref", f"--format={fmt}", "refs/heads"], ws  # type: ignore[arg-type]
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
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/remotes"], ws  # type: ignore[arg-type]
    )
    remote = [line.strip() for line in remote_out.splitlines()
              if line.strip() and not line.strip().endswith("/HEAD")]

    return JSONResponse(
        {"status": "ok", "current": current, "head_sha": head_sha, "local": local, "remote": remote}
    )


async def handle_git_show(request: Request) -> JSONResponse:
    ws, err = await _require_ws(request)
    if err:
        return err
    sha = request.path_params["sha"]

    SEP = "\x1f"
    fmt = f"%H{SEP}%h{SEP}%an{SEP}%ae{SEP}%aI{SEP}%s{SEP}%P"
    rc, meta_out, errout = await proc.run(
        ["git", "log", "-1", f"--format={fmt}", sha], ws  # type: ignore[arg-type]
    )
    if rc != 0 or not meta_out.strip():
        return _err(errout.strip() or f"unknown ref: {sha}", 404)
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
        ["git", "diff-tree", "--no-commit-id", "--root", "-r", "--name-status", sha], ws  # type: ignore[arg-type]
    )
    status_map = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
    files: list[dict] = []
    for line in files_out.strip().splitlines():
        fparts = line.split("\t", 2)
        if len(fparts) >= 2:
            files.append({"status": status_map.get(fparts[0][0], fparts[0]), "path": fparts[-1]})

    _, diff_out, _ = await proc.run(
        ["git", "show", "--format=", sha], ws  # type: ignore[arg-type]
    )

    return JSONResponse({"status": "ok", "commit": commit, "files": files, "diff": diff_out})


# ---------------------------------------------------------------------------
# /ws/{repo}/symbols
# ---------------------------------------------------------------------------

async def handle_symbols(request: Request) -> JSONResponse:
    repo = request.path_params["repo"]
    ws = find_repo(repo)
    if not ws:
        return _err(f"Workspace for '{repo}' not found", 404)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, symbols_cache.get_or_build, repo, ws)
    logger.info("[ws/symbols] %s: %d files", repo, result.get("fileCount", 0))
    return JSONResponse(result)


async def handle_symbols_invalidate(request: Request) -> JSONResponse:
    repo = request.path_params["repo"]
    symbols_cache.invalidate(repo)
    return JSONResponse({"status": "ok", "repo": repo})
