"""Shared pytest fixtures for hermes-adapter tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from aiohttp import web

from hermes_adapter.workspace.app import build_app
from hermes_adapter.workspace import symbols_cache


@pytest.fixture(autouse=True)
def _clear_symbols_cache():
    """Reset the module-level symbols cache between tests."""
    symbols_cache._cache.clear()
    yield
    symbols_cache._cache.clear()


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HERMES_WORKSPACE_DIR at a tmp dir for the duration of the test."""
    monkeypatch.setenv("HERMES_WORKSPACE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def make_repo(workspace_root: Path):
    """Factory: create an initialized git repo under the workspace root."""

    def _make(name: str = "demo", owner: str = "alice") -> Path:
        repo_dir = workspace_root / owner / name
        repo_dir.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir, check=True, env=env)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo_dir, check=True)
        (repo_dir / "README.md").write_text("# demo\n")
        subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_dir, check=True, env=env)
        return repo_dir

    return _make


@pytest.fixture
async def client(aiohttp_client, workspace_root):
    """aiohttp test client against the full workspace app."""
    app: web.Application = build_app()
    return await aiohttp_client(app)
