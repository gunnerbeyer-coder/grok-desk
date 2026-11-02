import pytest
from fastapi.testclient import TestClient

from desk.api import SnapshotRefused, build_local_app, build_payloads, maybe_push, serve_local, write_payloads
from desk.config import Config
from desk.db import DB


def _cfg(tmp_path, **overrides):
    data = dict(
        tg_token="123456789:AAHabcdefghijklmnopqrstuvwxyz0123456789",
        rpc_url="https://mainnet.example.test/rpc?api-key=abc123456789xyz",
        app_token="app-token-should-never-appear-in-json",
        data_path="a" * 40,
        root=str(tmp_path),
        pages_dir="docs",
        pages_push=False,
        db_path=str(tmp_path / "book.sqlite"),
    )
    data.update(overrides)
    return Config(**data)


def test_snapshot_has_no_secrets_and_refuses_a_leak(tmp_path):
    cfg = _cfg(tmp_path)
    db = DB(cfg.db_path)
    db.init()
    payloads = build_payloads(db, cfg)
    directory = tmp_path / "docs" / "d" / cfg.data_path
    write_payloads(directory, payloads)
    blob = (directory / "state.json").read_text(encoding="utf-8")
    for secret in (cfg.tg_token, cfg.rpc_url, cfg.app_token, cfg.data_path):
        assert secret not in blob
    assert "owner_chat_id" not in blob
    db.activity("note", cfg.tg_token, None, {})
    with pytest.raises(SnapshotRefused):
        build_payloads(db, cfg)


def test_push_is_opt_in_and_path_scoped():
    class Off:
        pages_push = False
        root = "."

    def boom(*_args, **_kwargs):
        raise AssertionError("push should stay off")

    assert maybe_push(Off(), "docs/d/" + "c" * 32, runner=boom) is False

    calls = []

    class On:
        pages_push = True
        root = "."

    def runner(args, **_kwargs):
        calls.append(list(args))

        class Result:
            returncode = 0

        return Result()

    rel = "docs/d/" + "d" * 32
    assert maybe_push(On(), rel, runner=runner) is True
    assert calls[0][:4] == ["git", "add", "-f", "--"]
    assert calls[1][:2] == ["git", "commit"]
    assert rel in calls[1]
    assert calls[2] == ["git", "push", "origin", "HEAD"]
    joined = " ".join(" ".join(c) for c in calls)
    assert "shell" not in joined
    with pytest.raises(SnapshotRefused):
        maybe_push(On(), "docs/d/short", runner=runner)


def test_local_preview_404s_and_refuses_a_public_bind(tmp_path):
    (tmp_path / "index.html").write_text("hello-desk", encoding="utf-8")
    (tmp_path / "d").mkdir()
    client = TestClient(build_local_app(tmp_path))
    assert client.get("/").status_code == 200
    assert "hello-desk" in client.get("/").text
    bare = client.get("/d/")
    assert bare.status_code == 404
    assert "Index of" not in bare.text
    assert "hello-desk" not in bare.text
    assert client.get("/d/missing/state.json").status_code == 404
    segment = "e" * 32
    folder = tmp_path / "d" / segment
    folder.mkdir()
    (folder / "state.json").write_text('{"ok":true}', encoding="utf-8")
    assert client.get(f"/d/{segment}/state.json").status_code == 200
    with pytest.raises(RuntimeError):
        serve_local("0.0.0.0", 9, tmp_path)


def test_bus_delivers_json_only():
    import asyncio

    from desk.bus import Bus

    async def go():
        bus = Bus(maxsize=2)
        queue = bus.subscribe("scan")
        bus.publish("scan", {"mint": "M"})
        assert await queue.get() == {"mint": "M"}
        with pytest.raises(TypeError):
            bus.publish("scan", ["nope"])

    asyncio.run(go())
