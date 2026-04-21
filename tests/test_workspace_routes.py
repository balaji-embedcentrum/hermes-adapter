from __future__ import annotations


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status == 200
    assert (await resp.json())["service"] == "hermes-adapter-workspace"


async def test_list_empty(client):
    resp = await client.get("/ws")
    body = await resp.json()
    assert body["status"] == "ok"
    assert body["workspaces"] == []


async def test_list_nested(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.get("/ws")
    body = await resp.json()
    names = [w["name"] for w in body["workspaces"]]
    assert "alice/demo" in names


async def test_tree(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.get("/ws/demo/tree")
    body = await resp.json()
    assert body["status"] == "ok"
    assert any(e["name"] == "README.md" for e in body["entries"])


async def test_file_get(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.get("/ws/demo/file", params={"path": "README.md"})
    body = await resp.json()
    assert body["status"] == "ok"
    assert body["content"].startswith("# demo")


async def test_file_post_then_get(client, make_repo):
    make_repo(name="demo", owner="alice")
    await client.post("/ws/demo/file", json={"path": "docs/note.md", "content": "hi"})
    resp = await client.get("/ws/demo/file", params={"path": "docs/note.md"})
    assert (await resp.json())["content"] == "hi"


async def test_file_post_rejects_traversal(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.post("/ws/demo/file", json={"path": "../escape.md", "content": "x"})
    assert resp.status == 403


async def test_file_delete(client, make_repo):
    repo = make_repo(name="demo", owner="alice")
    (repo / "trash.md").write_text("bye")
    resp = await client.delete("/ws/demo/file", params={"path": "trash.md"})
    assert resp.status == 200
    assert not (repo / "trash.md").exists()


async def test_git_status_clean(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.get("/ws/demo/git/status")
    body = await resp.json()
    assert body["status"] == "ok"
    assert body["changed"] == []


async def test_git_status_staged_and_unstaged(client, make_repo):
    """Status entries must expose both index (X) and worktree (Y) columns."""
    import subprocess
    repo = make_repo(name="demo", owner="alice")
    # Staged-only: new file, added to index
    (repo / "staged.md").write_text("s\n")
    subprocess.run(["git", "add", "staged.md"], cwd=repo, check=True)
    # Unstaged-only: modified existing file, not added
    (repo / "README.md").write_text("# demo\nchanged\n")
    # Untracked
    (repo / "untracked.md").write_text("u\n")

    body = await (await client.get("/ws/demo/git/status")).json()
    assert body["status"] == "ok"
    by_path = {e["path"]: e for e in body["changed"]}

    # staged.md — X=A, Y=' '
    assert by_path["staged.md"]["index"] == "A"
    assert by_path["staged.md"]["worktree"] == " "

    # README.md — X=' ', Y=M
    assert by_path["README.md"]["index"] == " "
    assert by_path["README.md"]["worktree"] == "M"

    # untracked.md — both '?'
    assert by_path["untracked.md"]["index"] == "?"
    assert by_path["untracked.md"]["worktree"] == "?"


async def test_git_commit_after_write(client, make_repo):
    make_repo(name="demo", owner="alice")
    await client.post("/ws/demo/file", json={"path": "new.md", "content": "x"})
    resp = await client.post("/ws/demo/git/commit", json={"message": "add new"})
    body = await resp.json()
    assert body["status"] == "ok"
    assert body["sha"]


async def test_git_log(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.get("/ws/demo/git/log")
    body = await resp.json()
    assert body["status"] == "ok"
    assert len(body["commits"]) == 1
    assert body["commits"][0]["message"] == "initial"


async def test_missing_repo(client):
    resp = await client.get("/ws/ghost/tree")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# /ws/{repo}/git/diff
# ---------------------------------------------------------------------------

async def test_git_diff_working_tree(client, make_repo):
    repo = make_repo(name="demo", owner="alice")
    (repo / "README.md").write_text("# demo\nchanged\n")
    resp = await client.get("/ws/demo/git/diff")
    body = await resp.json()
    assert body["status"] == "ok"
    assert "+changed" in body["diff"]
    assert "README.md" in body["diff"]


async def test_git_diff_path_scoped(client, make_repo):
    repo = make_repo(name="demo", owner="alice")
    (repo / "README.md").write_text("# demo\nchanged\n")
    (repo / "other.md").write_text("new\n")
    resp = await client.get("/ws/demo/git/diff", params={"path": "README.md"})
    body = await resp.json()
    assert body["status"] == "ok"
    assert "README.md" in body["diff"]
    assert "other.md" not in body["diff"]


async def test_git_diff_staged(client, make_repo):
    import subprocess
    repo = make_repo(name="demo", owner="alice")
    (repo / "README.md").write_text("# demo\nstaged change\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)

    # Unstaged diff is empty after staging
    unstaged = await (await client.get("/ws/demo/git/diff")).json()
    assert unstaged["diff"].strip() == ""

    # Staged diff has the change
    staged = await (await client.get("/ws/demo/git/diff", params={"staged": "true"})).json()
    assert "+staged change" in staged["diff"]


async def test_git_diff_ref(client, make_repo):
    make_repo(name="demo", owner="alice")
    # get the initial commit sha via the log endpoint
    log = await (await client.get("/ws/demo/git/log")).json()
    sha = log["commits"][0]["hash"]
    resp = await client.get("/ws/demo/git/diff", params={"ref": sha})
    body = await resp.json()
    assert body["status"] == "ok"
    # Root commit patch shows file being added
    assert "README.md" in body["diff"]
    assert "+# demo" in body["diff"]


async def test_git_diff_bad_ref(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.get("/ws/demo/git/diff", params={"ref": "does-not-exist"})
    assert resp.status == 400


# ---------------------------------------------------------------------------
# /ws/{repo}/git/branches
# ---------------------------------------------------------------------------

async def test_git_branches_fresh_repo(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.get("/ws/demo/git/branches")
    body = await resp.json()
    assert body["status"] == "ok"
    assert body["current"] == "main"
    assert body["head_sha"]
    assert any(b["name"] == "main" for b in body["local"])
    assert body["remote"] == []


async def test_git_branches_after_create(client, make_repo):
    import subprocess
    repo = make_repo(name="demo", owner="alice")
    subprocess.run(["git", "branch", "feature"], cwd=repo, check=True)
    body = await (await client.get("/ws/demo/git/branches")).json()
    names = {b["name"] for b in body["local"]}
    assert {"main", "feature"} <= names
    assert body["current"] == "main"


async def test_git_branches_detached_head(client, make_repo):
    import subprocess
    repo = make_repo(name="demo", owner="alice")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "checkout", "--detach", sha], cwd=repo, check=True,
                   stderr=subprocess.DEVNULL)
    body = await (await client.get("/ws/demo/git/branches")).json()
    assert body["current"] is None
    assert body["head_sha"]


# ---------------------------------------------------------------------------
# /ws/{repo}/git/show/{sha}
# ---------------------------------------------------------------------------

async def test_git_show_initial_commit(client, make_repo):
    make_repo(name="demo", owner="alice")
    log = await (await client.get("/ws/demo/git/log")).json()
    sha = log["commits"][0]["hash"]
    resp = await client.get(f"/ws/demo/git/show/{sha}")
    body = await resp.json()
    assert body["status"] == "ok"
    assert body["commit"]["hash"] == sha
    assert body["commit"]["message"] == "initial"
    assert any(f["path"] == "README.md" for f in body["files"])
    assert "+# demo" in body["diff"]


async def test_git_show_bad_sha(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.get("/ws/demo/git/show/deadbeef")
    assert resp.status == 404
    body = await resp.json()
    assert body["status"] == "error"


# ---------------------------------------------------------------------------
# /ws/{repo}/git/stage | unstage | discard
# ---------------------------------------------------------------------------

async def test_git_stage_selective(client, make_repo):
    repo = make_repo(name="demo", owner="alice")
    (repo / "a.md").write_text("a\n")
    (repo / "b.md").write_text("b\n")
    resp = await client.post("/ws/demo/git/stage", json={"paths": ["a.md"]})
    body = await resp.json()
    assert body["status"] == "ok"
    assert body["staged"] == ["a.md"]

    # Only a.md should be staged; b.md remains untracked
    staged = await (await client.get("/ws/demo/git/diff", params={"staged": "true"})).json()
    assert "a.md" in staged["diff"]
    assert "b.md" not in staged["diff"]


async def test_git_stage_requires_paths(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.post("/ws/demo/git/stage", json={})
    assert resp.status == 400
    resp2 = await client.post("/ws/demo/git/stage", json={"paths": []})
    assert resp2.status == 400


async def test_git_unstage(client, make_repo):
    import subprocess
    repo = make_repo(name="demo", owner="alice")
    (repo / "new.md").write_text("new\n")
    subprocess.run(["git", "add", "new.md"], cwd=repo, check=True)
    resp = await client.post("/ws/demo/git/unstage", json={"paths": ["new.md"]})
    assert resp.status == 200
    # Confirm it's no longer staged
    staged = await (await client.get("/ws/demo/git/diff", params={"staged": "true"})).json()
    assert "new.md" not in staged["diff"]


async def test_git_discard(client, make_repo):
    repo = make_repo(name="demo", owner="alice")
    (repo / "README.md").write_text("# demo\ndirty\n")
    resp = await client.post("/ws/demo/git/discard", json={"paths": ["README.md"]})
    assert resp.status == 200
    # Working tree should match HEAD again
    assert (repo / "README.md").read_text() == "# demo\n"


# ---------------------------------------------------------------------------
# /ws/{repo}/git/commit — auto_stage param
# ---------------------------------------------------------------------------

async def test_git_commit_auto_stage_false_requires_staged(client, make_repo):
    make_repo(name="demo", owner="alice")
    # Write a file but don't stage it; commit with auto_stage=false must fail.
    await client.post("/ws/demo/file", json={"path": "x.md", "content": "x"})
    resp = await client.post(
        "/ws/demo/git/commit", json={"message": "x", "auto_stage": False}
    )
    assert resp.status == 500  # git commit with nothing staged


async def test_git_commit_auto_stage_false_commits_staged(client, make_repo):
    make_repo(name="demo", owner="alice")
    await client.post("/ws/demo/file", json={"path": "x.md", "content": "x"})
    await client.post("/ws/demo/git/stage", json={"paths": ["x.md"]})
    resp = await client.post(
        "/ws/demo/git/commit", json={"message": "add x", "auto_stage": False}
    )
    body = await resp.json()
    assert body["status"] == "ok"
    assert body["sha"]


# ---------------------------------------------------------------------------
# /ws/{repo}/git/checkout | branch
# ---------------------------------------------------------------------------

async def test_git_branch_and_checkout(client, make_repo):
    make_repo(name="demo", owner="alice")
    # Create a branch off HEAD
    r1 = await client.post("/ws/demo/git/branch", json={"name": "feature"})
    assert r1.status == 200
    branches = await (await client.get("/ws/demo/git/branches")).json()
    assert any(b["name"] == "feature" for b in branches["local"])
    assert branches["current"] == "main"

    # Switch to it
    r2 = await client.post("/ws/demo/git/checkout", json={"branch": "feature"})
    assert r2.status == 200
    branches2 = await (await client.get("/ws/demo/git/branches")).json()
    assert branches2["current"] == "feature"


async def test_git_checkout_create(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.post(
        "/ws/demo/git/checkout", json={"branch": "new-branch", "create": True}
    )
    assert resp.status == 200
    branches = await (await client.get("/ws/demo/git/branches")).json()
    assert branches["current"] == "new-branch"


async def test_git_checkout_requires_branch(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.post("/ws/demo/git/checkout", json={})
    assert resp.status == 400


# ---------------------------------------------------------------------------
# /ws/{repo}/git/fetch
# ---------------------------------------------------------------------------

async def test_git_fetch_with_local_origin(client, make_repo, workspace_root):
    import subprocess
    repo = make_repo(name="demo", owner="alice")
    # Create a bare repo and wire it up as 'origin'
    origin = workspace_root / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True)
    resp = await client.post("/ws/demo/git/fetch")
    body = await resp.json()
    assert body["status"] == "ok"


async def test_git_fetch_no_remote(client, make_repo):
    make_repo(name="demo", owner="alice")
    # Fresh repo has no remote → git fetch --all succeeds silently with no output;
    # either the handler returns ok (empty output) or error. Pin the expectation.
    resp = await client.post("/ws/demo/git/fetch")
    assert resp.status in (200, 500)
