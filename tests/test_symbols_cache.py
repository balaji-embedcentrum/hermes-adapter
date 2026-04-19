from __future__ import annotations

from pathlib import Path


async def test_symbols_empty(client, make_repo):
    make_repo(name="demo", owner="alice")
    resp = await client.get("/ws/demo/symbols")
    body = await resp.json()
    assert body["fileCount"] == 0
    assert body["files"] == []


async def test_symbols_picks_up_sylang_ext(client, make_repo):
    repo = make_repo(name="demo", owner="alice")
    (repo / "model.itm").write_text("item TestItem\n")
    (repo / "not-sylang.md").write_text("ignored")
    resp = await client.get("/ws/demo/symbols")
    body = await resp.json()
    assert body["fileCount"] == 1
    assert body["files"][0]["path"] == "model.itm"
    assert body["files"][0]["content"] == "item TestItem\n"


async def test_symbols_cache_invalidated_on_write(client, make_repo):
    repo = make_repo(name="demo", owner="alice")
    (repo / "a.itm").write_text("v1")
    # prime the cache
    r1 = await (await client.get("/ws/demo/symbols")).json()
    assert r1["fileCount"] == 1

    # writing a new Sylang file through the API must bust the cache
    await client.post("/ws/demo/file", json={"path": "b.itm", "content": "v2"})
    r2 = await (await client.get("/ws/demo/symbols")).json()
    assert r2["fileCount"] == 2


async def test_symbols_explicit_invalidate(client, make_repo):
    repo = make_repo(name="demo", owner="alice")
    (repo / "a.itm").write_text("v1")
    await (await client.get("/ws/demo/symbols")).json()

    # Writing directly to disk bypasses the cache bust; /invalidate fixes it.
    (repo / "c.itm").write_text("v3")
    await client.post("/ws/demo/symbols/invalidate")
    body = await (await client.get("/ws/demo/symbols")).json()
    assert body["fileCount"] == 2


async def test_symbols_ignores_node_modules(client, make_repo):
    repo = make_repo(name="demo", owner="alice")
    nm = repo / "node_modules" / "lib"
    nm.mkdir(parents=True)
    (nm / "bundled.itm").write_text("noise")
    (repo / "real.itm").write_text("real")

    body = await (await client.get("/ws/demo/symbols")).json()
    paths = [f["path"] for f in body["files"]]
    assert "real.itm" in paths
    assert not any("node_modules" in p for p in paths)
