"""In-process cache for Sylang symbols — batch file delivery with TTL.

The cache maps ``repo`` → ``(timestamp, payload)`` and is invalidated either
by TTL expiry (5 minutes) or explicitly by ``invalidate(repo)``. Route
handlers that modify files call ``invalidate(repo)`` so the next ``/symbols``
call picks up the change.
"""

from __future__ import annotations

import os
import time
from typing import Any

_TTL = 300  # seconds

_SYLANG_EXTS = frozenset(
    {
        ".req",
        ".agt",
        ".blk",
        ".fml",
        ".fun",
        ".haz",
        ".ifc",
        ".itm",
        ".ple",
        ".sam",
        ".seq",
        ".sgl",
        ".smd",
        ".spec",
        ".spr",
        ".tst",
        ".ucd",
        ".vcf",
        ".vml",
        ".fta",
        ".flr",
        ".dash",
    }
)
_SYLANG_IGNORED = frozenset(
    {
        ".git",
        "node_modules",
        ".next",
        "dist",
        ".turbo",
        ".cache",
        "__pycache__",
    }
)

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def invalidate(repo: str) -> None:
    _cache.pop(repo, None)


def _walk(workspace: str) -> list[dict[str, str]]:
    """Walk *workspace* depth-limited (8) and return [{path, content}] for Sylang files."""
    results: list[dict[str, str]] = []

    def walk(directory: str, depth: int) -> None:
        if depth > 8:
            return
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    if entry.name in _SYLANG_IGNORED:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        walk(entry.path, depth + 1)
                    elif entry.is_file(follow_symlinks=False):
                        _, ext = os.path.splitext(entry.name)
                        if ext in _SYLANG_EXTS:
                            try:
                                with open(entry.path, "r", encoding="utf-8", errors="replace") as f:
                                    content = f.read()
                                rel = os.path.relpath(entry.path, workspace).replace(os.sep, "/")
                                results.append({"path": rel, "content": content})
                            except OSError:
                                pass
        except (PermissionError, OSError):
            pass

    walk(workspace, 0)
    return results


def get_or_build(repo: str, workspace: str) -> dict[str, Any]:
    """Return cached payload for *repo* or walk *workspace* and cache a fresh one."""
    now = time.time()
    cached = _cache.get(repo)
    if cached and (now - cached[0]) < _TTL:
        return cached[1]

    files = _walk(workspace)
    result = {
        "files": files,
        "fileCount": len(files),
        "cachedAt": int(now * 1000),
    }
    _cache[repo] = (now, result)
    return result
