"""End-to-end CLI tests for init / agent add / agent list / agent remove."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect the adapter's default home to a tmp dir."""
    home = tmp_path / "adapter-home"
    monkeypatch.setenv("HERMES_ADAPTER_HOME", str(home))

    # Reload the manifest module so DEFAULT_* constants pick up the env var
    import importlib

    import hermes_adapter.manifest as manifest_mod

    importlib.reload(manifest_mod)
    import hermes_adapter.cli as cli_mod

    importlib.reload(cli_mod)
    yield home, cli_mod


def test_init_then_add_list_remove(cli_home, capsys):
    home, cli = cli_home

    # init
    rc = cli.main(["init"])
    assert rc == 0
    manifest = home / "agents.yaml"
    assert manifest.exists()
    captured = capsys.readouterr()
    assert "a2a bearer" in captured.out

    # add alpha
    rc = cli.main(
        [
            "agent", "add", "alpha",
            "--model", "anthropic/claude-sonnet-4.6",
            "--key", "sk-ant-test",
            "--description", "Code review",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "added agent 'alpha'" in captured.out

    # add beta on auto-picked port
    rc = cli.main(
        ["agent", "add", "beta", "--model", "openai/gpt-5", "--key", "sk-test"]
    )
    assert rc == 0

    # list shows both
    rc = cli.main(["agent", "list"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "alpha" in captured.out
    assert "beta" in captured.out
    assert "9001" in captured.out
    assert "9002" in captured.out

    # remove beta (without purge keeps its folder)
    rc = cli.main(["agent", "remove", "beta"])
    assert rc == 0
    capsys.readouterr()  # drain the remove-command output

    rc = cli.main(["agent", "list"])
    captured = capsys.readouterr()
    assert "alpha" in captured.out
    assert "beta" not in captured.out


def test_init_refuses_overwrite_without_force(cli_home, capsys):
    home, cli = cli_home
    cli.main(["init"])
    capsys.readouterr()

    rc = cli.main(["init"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "already exists" in captured.err


def test_init_with_force_regenerates(cli_home, capsys):
    home, cli = cli_home
    cli.main(["init"])
    first = (home / "agents.yaml").read_text()
    capsys.readouterr()

    cli.main(["init", "--force"])
    second = (home / "agents.yaml").read_text()
    # bearer token is regenerated
    assert first != second


def test_agent_add_rejects_duplicate(cli_home, capsys):
    home, cli = cli_home
    cli.main(["init"])
    cli.main(
        ["agent", "add", "alpha", "--model", "openai/gpt-5", "--key", "x"]
    )
    capsys.readouterr()

    rc = cli.main(
        ["agent", "add", "alpha", "--model", "openai/gpt-5", "--key", "x"]
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "already exists" in captured.err
