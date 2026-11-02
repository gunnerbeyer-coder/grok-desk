"""One asyncio process: five agents, Telegram long poll, Plan B JSON writer.

No public port. LOCAL_PREVIEW=1 binds 127.0.0.1 only.
The book is paper unless you switch to live, and live still does not send an order.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

import httpx

from desk import __version__
from desk.agents import analyst, auditor, executor, risk, scanner
from desk.api import build_local_app, run as writer_run
from desk.bus import Bus
from desk.config import load_config
from desk.db import DB
from desk.runtime import Desk
from desk.telegram import run as telegram_run
from desk.vendors import Vendors

log = logging.getLogger("desk")


def _logging() -> None:
    root = Path(__file__).resolve().parents[1]
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "desk.log", encoding="utf-8"),
        ],
    )


async def _local(cfg, stop: asyncio.Event) -> None:
    if cfg.local_host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("local preview refuses a public bind")
    import uvicorn

    directory = Path(cfg.root) / cfg.pages_dir
    server = uvicorn.Server(
        uvicorn.Config(
            build_local_app(directory),
            host=cfg.local_host,
            port=cfg.local_port,
            log_level="warning",
        )
    )
    task = asyncio.create_task(server.serve())
    await stop.wait()
    server.should_exit = True
    await task


async def amain() -> None:
    cfg = load_config()
    db = DB(cfg.db_path)
    db.init()
    if cfg.data_path and not cfg.snapshot_enabled():
        log.warning("DATA_PATH ignored; generate 32+ url-safe characters")
    log.info(
        "desk %s snapshot=%s telegram=%s rpc=%s push=%s",
        __version__,
        "on" if cfg.snapshot_enabled() else "off",
        "on" if bool(cfg.tg_token) else "off",
        "on" if bool(cfg.rpc_url) else "off",
        "on" if cfg.pages_push else "off",
    )
    stop = asyncio.Event()
    # A missing per-call timeout must not park a connection until the kernel gives up.
    async with httpx.AsyncClient(
        headers={"user-agent": "grok-desk"},
        timeout=httpx.Timeout(20.0, pool=5.0),
    ) as http:
        ctx = Desk(cfg, db, Bus(), Vendors(http, cfg.rpc_url))
        tasks = [
            asyncio.create_task(scanner.run(ctx, stop), name="scanner"),
            asyncio.create_task(auditor.run(ctx, stop), name="auditor"),
            asyncio.create_task(analyst.run(ctx, stop), name="analyst"),
            asyncio.create_task(executor.run(ctx, stop), name="executor"),
            asyncio.create_task(risk.run(ctx, stop), name="risk"),
            asyncio.create_task(telegram_run(ctx, stop), name="telegram"),
            asyncio.create_task(writer_run(ctx, stop), name="writer"),
        ]
        if cfg.local_preview:
            tasks.append(asyncio.create_task(_local(cfg, stop), name="preview"))
        loop = asyncio.get_running_loop()

        def _shutdown() -> None:
            stop.set()
            for task in tasks:
                task.cancel()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _shutdown)
            except NotImplementedError:
                pass
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            stop.set()
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            db.close()


def main() -> int:
    _logging()
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
