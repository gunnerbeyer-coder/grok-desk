import asyncio
import json
import time

from websockets.exceptions import ConnectionClosedError

from desk.agents.scanner import (
    LiveSocket,
    close_socket,
    interruptible_sleep,
    scanner_is_stale,
    watchdog_step,
    _socket_loop,
)
from desk.db import DB


class _Transport:
    def __init__(self, owner):
        self.owner = owner

    def abort(self):
        self.owner.aborted = True
        if not self.owner.done.done():
            self.owner.done.set_result(None)

    def is_closing(self):
        return self.owner.aborted


class _Socket:
    def __init__(self):
        self.aborted = False
        self.done = asyncio.get_running_loop().create_future()
        self.transport = _Transport(self)
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        await self.done
        raise ConnectionClosedError(None, None)

    async def close(self):
        await asyncio.sleep(30)


def test_scanner_stale_clock():
    live = LiveSocket()
    assert scanner_is_stale(live, now=live.touched + 10, after=45) is False
    assert scanner_is_stale(live, now=live.touched + 46, after=45) is True


def test_backoff_sleep_wakes_when_forced():
    async def go():
        live = LiveSocket()
        live.force.set()
        started = time.monotonic()
        await interruptible_sleep(30, asyncio.Event(), live)
        return time.monotonic() - started

    assert asyncio.run(go()) < 0.5


def test_close_socket_times_out_and_lets_the_loop_run():
    async def go():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.05)

        ws = _Socket()
        task = asyncio.create_task(ticker())
        await asyncio.sleep(0.05)
        await close_socket(ws, timeout=0.2)
        task.cancel()
        return ticks, ws.aborted

    ticks, aborted = asyncio.run(go())
    assert ticks >= 2
    assert aborted


def test_watchdog_aborts_a_stuck_recv(tmp_path):
    async def go():
        db = DB(str(tmp_path / "book.sqlite"))
        db.init()
        live = LiveSocket()
        live.touched = time.monotonic() - 100
        ws = _Socket()
        live.ws = ws

        async def reader():
            await ws.recv()

        reader_task = asyncio.create_task(reader())
        await asyncio.sleep(0)

        class Ctx:
            pass

        ctx = Ctx()
        ctx.db = db
        fired = await watchdog_step(ctx, live)
        with_exc = None
        try:
            await asyncio.wait_for(reader_task, timeout=1)
        except ConnectionClosedError as exc:
            with_exc = exc
        detail = [row["detail"] for row in db.heartbeats() if row["agent"] == "scanner"]
        return fired, ws.aborted, detail, with_exc is not None

    fired, aborted, detail, closed = asyncio.run(go())
    assert fired is True
    assert aborted is True
    assert closed is True
    assert "watchdog" in detail


def test_socket_loop_reconnects_after_close_and_does_not_stall(tmp_path):
    async def go():
        db = DB(str(tmp_path / "book.sqlite"))
        db.init()
        made = []

        class WS(_Socket):
            def __init__(self):
                super().__init__()
                made.append(self)

            async def recv(self):
                if len(made) == 1:
                    raise ConnectionClosedError(None, None)
                self.sent.append("recv-open")
                await asyncio.sleep(0.05)
                return json.dumps(
                    {
                        "mint": "MintReconnect111111111111111111111111111",
                        "signature": "sig",
                        "txType": "migrate",
                        "pool": "pump-amm",
                    }
                )

            async def close(self):
                return None

        async def connect():
            return WS()

        class Ctx:
            pass

        ctx = Ctx()
        ctx.db = db
        queue = asyncio.Queue()
        stop = asyncio.Event()
        live = LiveSocket()
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.02)

        tick = asyncio.create_task(ticker())
        task = asyncio.create_task(_socket_loop(ctx, queue, stop, live, connect=connect))
        item = await asyncio.wait_for(queue.get(), timeout=3)
        stop.set()
        live.abort()
        await asyncio.wait_for(task, timeout=2)
        tick.cancel()
        return item, len(made), ticks, [s for ws in made for s in ws.sent]

    item, sockets, ticks, sent = asyncio.run(go())
    assert item["pool_type"] == "pump-amm"
    assert sockets >= 2
    assert ticks >= 3
    assert any("subscribeMigration" in text for text in sent)
