"""Locate a workspace directory for a given repo name.

The workspace root is ``HERMES_WORKSPACE_DIR`` (default ``/workspaces``).

Lookup order:
  1. ``login/repo`` — if *repo* contains ``/``, resolve ``{root}/{login}/{repo}``.
     Also try the parent of ``{root}`` when ``{root}`` ends with ``/active``
     (active-workspace symlink isolation pattern).
  2. Direct child — ``{root}/{repo}`` (flat layout / symlink-isolated).
  3. Nested scan — ``{root}/{any_subdir}/{repo}`` for legacy multi-user layouts.
     Skipped when ``{root}`` is symlink-isolated (ends with ``/active``).
  4. Legacy fallbacks — ``/root/{repo}``, ``~/{repo}``, etc.
"""

from __future__ import annotations

import os


def workspace_root() -> str:
    return os.environ.get("HERMES_WORKSPACE_DIR", "/workspaces")


def find_repo(repo: str) -> str | None:
    """Return the absolute path of *repo*'s workspace, or None if not found."""
    root = workspace_root()

    # 1. "login/repo" — direct user-scoped lookup
    if "/" in repo:
        candidate = os.path.join(root, repo)
        if os.path.isdir(candidate):
            return candidate
        parent_root = os.path.dirname(root) if root.rstrip("/").endswith("/active") else None
        if parent_root:
            candidate = os.path.join(parent_root, repo)
            if os.path.isdir(candidate):
                return candidate
        return None

    # 2. Direct child — flat layout / active-symlink isolation
    direct = os.path.join(root, repo)
    if os.path.isdir(direct):
        return direct

    # Symlink-isolated mode: do not scan siblings (cross-user leak prevention)
    if root.rstrip("/").endswith("/active"):
        return None

    # 3. Nested scan — {root}/{subdir}/{repo}
    if os.path.isdir(root):
        try:
            for entry in sorted(os.scandir(root), key=lambda e: e.name):
                if entry.is_dir():
                    candidate = os.path.join(entry.path, repo)
                    if os.path.isdir(candidate):
                        return candidate
        except PermissionError:
            pass

    # 4. Legacy paths
    for p in (
        f"/root/{repo}",
        f"/workspace/{repo}",
        f"/workspaces/{repo}",
        f"/home/ubuntu/{repo}",
        f"/home/user/{repo}",
        os.path.expanduser(f"~/{repo}"),
    ):
        if os.path.isdir(p):
            return p
    return None


def resolve_safe_path(workspace: str, rel: str) -> str | None:
    """Resolve ``workspace/rel`` and reject paths that escape the workspace.

    Returns the absolute path on success, or None if *rel* would traverse
    outside *workspace* via ``..`` or absolute components.
    """
    abs_path = os.path.realpath(os.path.join(workspace, rel))
    if not abs_path.startswith(os.path.realpath(workspace)):
        return None
    return abs_path
