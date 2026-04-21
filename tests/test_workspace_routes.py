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
