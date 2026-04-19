from __future__ import annotations

from pathlib import Path


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
