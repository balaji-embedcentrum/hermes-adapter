from __future__ import annotations

from pathlib import Path

from hermes_adapter.workspace.repo_finder import find_repo, resolve_safe_path


def test_find_repo_flat(workspace_root: Path):
    repo = workspace_root / "my-proj"
    repo.mkdir()
    assert find_repo("my-proj") == str(repo)


def test_find_repo_nested(workspace_root: Path):
    repo = workspace_root / "alice" / "my-proj"
    repo.mkdir(parents=True)
    assert find_repo("my-proj") == str(repo)


def test_find_repo_login_slash(workspace_root: Path):
    repo = workspace_root / "alice" / "my-proj"
    repo.mkdir(parents=True)
    assert find_repo("alice/my-proj") == str(repo)


def test_find_repo_missing(workspace_root: Path):
    assert find_repo("nope") is None


def test_active_symlink_refuses_sibling_scan(tmp_path: Path, monkeypatch):
    """With /active isolation the scanner must not find sibling users' repos."""
    mount = tmp_path / "mount"
    (mount / "alice" / "shared").mkdir(parents=True)
    (mount / "bob" / "private").mkdir(parents=True)

    active = mount / "active"
    active.symlink_to(mount / "alice")

    monkeypatch.setenv("HERMES_WORKSPACE_DIR", str(active))

    # alice's own repo via absolute lookup works
    assert find_repo("shared") == str(active / "shared")
    # bob's repo must NOT be reachable via nested scan
    assert find_repo("private") is None


def test_resolve_safe_path_blocks_traversal(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("ok")

    assert resolve_safe_path(str(ws), "a.txt") == str((ws / "a.txt").resolve())
    assert resolve_safe_path(str(ws), "../outside") is None
    assert resolve_safe_path(str(ws), "/etc/passwd") is None
