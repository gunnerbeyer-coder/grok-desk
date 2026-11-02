"""Plan B writer. Static JSON under /d/{DATA_PATH}/. No public listen unless asked.

LOCAL_PREVIEW binds 127.0.0.1 only. The phone polls the files GitHub Pages serves.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from desk.config import data_path_ok
from desk.links import SIGNUP_URL, coin_url, profile_url, require_canonical
from desk.report import book_value

log = logging.getLogger("desk.api")

AGENTS = ("scanner", "auditor", "analyst", "executor", "risk", "telegram", "writer")
WEB_FILES = (
    "index.html",
    "manifest.json",
    "404.html",
    "apple-touch-icon.png",
    "icon-192.png",
    "icon-512.png",
    "css/app.css",
    "js/app.js",
    "js/chart.js",
)
BANNED_KEYS = {
    "tg_token",
    "rpc_url",
    "data_path",
    "app_token",
    "vapid_private_key",
    "typesafe_api_key",
    "api_key",
    "authorization",
    "owner_chat_id",
}


class SnapshotRefused(RuntimeError):
    pass


def scrub(obj, secrets: set[str]):
    if isinstance(obj, dict):
        clean = {}
        for key, value in obj.items():
            if str(key).lower() in BANNED_KEYS:
                continue
            clean[key] = scrub(value, secrets)
        return clean
    if isinstance(obj, list):
        return [scrub(item, secrets) for item in obj]
    if isinstance(obj, str):
        for secret in secrets:
            if secret and secret in obj:
                raise SnapshotRefused("snapshot refused")
        return obj
    if isinstance(obj, float) and obj != obj:
        return None
    return obj


def _dumps(obj) -> str:
    return json.dumps(obj, allow_nan=False, separators=(",", ":"))


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def build_local_app(directory: Path) -> FastAPI:
    """Static files. Directories and unknown paths are 404. No listing."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def bare_secret_root(request, call_next):
        path = request.url.path.rstrip("/") or "/"
        if path == "/d":
            return PlainTextResponse("Not found", status_code=404)
        response = await call_next(request)
        if response.status_code == 404:
            return PlainTextResponse("Not found", status_code=404)
        return response

    app.mount("/", StaticFiles(directory=str(directory), html=True), name="pages")
    return app


def serve_local(host: str, port: int, directory: Path) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("local preview refuses a public bind")
    import uvicorn

    uvicorn.run(build_local_app(directory), host=host, port=port, log_level="warning")


def sync_web(root: Path, pages_dir: str) -> None:
    web = root / "web"
    dest = root / pages_dir
    dest.mkdir(parents=True, exist_ok=True)
    for rel in WEB_FILES:
        src = web / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    nojekyll = dest / ".nojekyll"
    if not nojekyll.exists():
        nojekyll.write_text("", encoding="utf-8")
    keep = dest / "d" / ".gitkeep"
    keep.parent.mkdir(parents=True, exist_ok=True)
    if not keep.exists():
        keep.write_text("", encoding="utf-8")


def snapshot_dir(cfg) -> Path | None:
    if not cfg.snapshot_enabled():
        return None
    root = Path(cfg.root)
    pages = (root / cfg.pages_dir).resolve()
    if not str(pages).startswith(str(root.resolve())):
        raise SnapshotRefused("pages dir escapes the repo")
    segment = cfg.data_path
    if not data_path_ok(segment):
        return None
    target = (pages / "d" / segment).resolve()
    if not str(target).startswith(str(pages)):
        raise SnapshotRefused("data path escapes pages")
    return target


def _agent_rows(db) -> list[dict]:
    found = {row["agent"]: row for row in db.heartbeats()}
    rows = []
    now = int(time.time())
    for name in AGENTS:
        beat = found.get(name)
        if not beat:
            rows.append({"name": name, "status": "offline", "ts": None, "detail": ""})
            continue
        age = now - int(beat["ts"])
        status = beat["status"] or "offline"
        if age > 90 and status == "ok":
            status = "stale"
        rows.append(
            {
                "name": name,
                "status": status,
                "ts": beat["ts"],
                "detail": beat.get("detail") or "",
            }
        )
    return rows


def _coin_row(db, coin: dict) -> dict:
    last = db.latest_price(coin["mint"])
    verdict = db.latest_verdict(coin["mint"])
    url = require_canonical(coin_url(coin["mint"]))
    return {
        "mint": coin["mint"],
        "symbol": coin.get("symbol") or "",
        "name": coin.get("name") or "",
        "status": coin.get("status"),
        "reason": coin.get("kill_reason") or "",
        "price": None if not last else last.get("price_usd"),
        "liq": None if not last else last.get("liq_usd"),
        "score": None if not verdict else verdict.get("score"),
        "url": url,
        "updated_at": coin.get("updated_at"),
    }


def build_payloads(db, cfg) -> dict:
    now = int(time.time())
    book = book_value(db)
    handle = db.get_setting("fomo_handle")
    settings = db.public_settings()
    coins = [_coin_row(db, coin) for coin in db.recent_coins(40)]
    positions = []
    for pos in book["positions"]:
        pos = dict(pos)
        pos["url"] = require_canonical(coin_url(pos["mint"]))
        positions.append(pos)
    audit_mint = db.latest_audit_mint()
    last_audit = None
    if audit_mint:
        coin = db.coin(audit_mint) or {}
        last_audit = {
            "mint": audit_mint,
            "symbol": coin.get("symbol") or "",
            "layers": db.audits_for(audit_mint),
        }
    state = {
        "ts": now,
        "mode": settings.get("mode"),
        "paused": settings.get("paused") == "1",
        "analyst_mode": settings.get("analyst_mode"),
        "notifier": settings.get("notifier"),
        "handle": handle,
        "signup_url": require_canonical(SIGNUP_URL),
        "profile_url": require_canonical(profile_url(handle)),
        "balance": {
            "equity": round(book["equity"], 4),
            "cash": round(book["cash"], 4),
            "pnl": round(book["pnl"], 4),
            "starting": round(book["starting"], 4),
        },
        "settings": settings,
        "agents": _agent_rows(db),
        "positions": positions,
        "coins": coins,
        "last_audit": last_audit,
    }
    series = {}
    mints = []
    for coin in coins:
        mints.append(coin["mint"])
    for pos in positions:
        if pos["mint"] not in mints:
            mints.append(pos["mint"])
    for mint in mints[:24]:
        points = db.price_series(mint, 240)
        if points:
            series[mint] = points
    chart = {"ts": now, "series": series}
    activity = {
        "ts": now,
        "items": [
            {"ts": row["ts"], "kind": row["kind"], "mint": row["mint"], "text": row["message"]}
            for row in db.recent_activity(80)
        ],
    }
    coin_files = {}
    for coin in db.recent_coins(24):
        mint = coin["mint"]
        verdict = db.latest_verdict(mint)
        verdict_out = None
        if verdict:
            verdict_out = {
                "alive": bool(verdict["alive"]),
                "organic": bool(verdict["organic"]),
                "flush": bool(verdict["flush"]),
                "chase": bool(verdict["chase"]),
                "gate": bool(verdict["gate"]),
                "score": verdict["score"],
                "reason": verdict["reason"],
            }
        position = next((p for p in positions if p["mint"] == mint), None)
        coin_files[mint] = {
            "ts": now,
            "mint": mint,
            "symbol": coin.get("symbol") or "",
            "name": coin.get("name") or "",
            "status": coin.get("status"),
            "reason": coin.get("kill_reason") or "",
            "pair": coin.get("pair_address"),
            "pool_type": coin.get("pool_type"),
            "url": require_canonical(coin_url(mint)),
            "layers": db.audits_for(mint),
            "verdict": verdict_out,
            "position": position,
            "series": db.price_series(mint, 240),
        }
    secrets = cfg.secret_values() if hasattr(cfg, "secret_values") else set()
    return {
        "state": scrub(state, secrets),
        "chart": scrub(chart, secrets),
        "activity": scrub(activity, secrets),
        "coins": {mint: scrub(body, secrets) for mint, body in coin_files.items()},
    }


def write_payloads(directory: Path, payloads: dict) -> None:
    atomic_write(directory / "state.json", _dumps(payloads["state"]))
    atomic_write(directory / "chart.json", _dumps(payloads["chart"]))
    atomic_write(directory / "activity.json", _dumps(payloads["activity"]))
    coin_dir = directory / "coin"
    for mint, body in payloads["coins"].items():
        if not mint or "/" in mint or mint in {".", ".."}:
            continue
        atomic_write(coin_dir / f"{mint}.json", _dumps(body))


def maybe_push(cfg, rel: str, runner=None) -> bool:
    """Commit and push only the snapshot directory. Off unless PAGES_PUSH=1."""
    if not getattr(cfg, "pages_push", False):
        return False
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise SnapshotRefused("snapshot path")
    if not data_path_ok(rel_path.name):
        raise SnapshotRefused("snapshot segment")
    root = Path(cfg.root)
    run = runner or subprocess.run

    def call(args):
        # timeout kills a stalled git process. The writer also runs this off
        # the event loop so the kill wait cannot freeze the other agents.
        return run(args, cwd=str(root), capture_output=True, timeout=20)

    added = call(["git", "add", "-f", "--", rel])
    if getattr(added, "returncode", 1) != 0:
        log.warning("pages add failed")
        return False
    committed = call(["git", "commit", "-m", "Update desk snapshot", "--", rel])
    if getattr(committed, "returncode", 1) != 0:
        return False
    pushed = call(["git", "push", "origin", "HEAD"])
    if getattr(pushed, "returncode", 1) != 0:
        log.warning("pages push failed")
        return False
    log.info("pages push ok")
    return True


async def _refresh_remaining(ctx) -> None:
    open_mints = {p["mint"] for p in ctx.db.open_positions()}
    waiting = {c["mint"] for c in ctx.db.coins_by_status(("waiting",))}
    covered = open_mints | waiting
    want = []
    for coin in ctx.db.recent_coins(30):
        if coin["mint"] in covered:
            continue
        if coin.get("status") in {"killed"}:
            continue
        want.append(coin["mint"])
    if not want:
        return
    from desk.agents.scanner import as_float, normalize_pair

    pairs = await ctx.vendors.dexscreener(want[:30])
    seen = set()
    for pair in pairs:
        norm = normalize_pair(pair)
        mint = norm.get("baseMint")
        if not mint or mint in seen or mint not in want:
            continue
        seen.add(mint)
        ctx.db.insert_price(mint, as_float(norm.get("priceUsd")), as_float(norm.get("liquidity_usd")), "api")


async def run(ctx, stop: asyncio.Event) -> None:
    root = Path(ctx.cfg.root)
    try:
        sync_web(root, ctx.cfg.pages_dir)
    except Exception as exc:
        log.warning("web sync failed: %s", type(exc).__name__)
    last_price = 0.0
    last_push = 0.0
    while not stop.is_set():
        try:
            now = time.time()
            if now - last_price >= 60:
                await _refresh_remaining(ctx)
                last_price = now
                ctx.db.prune_prices(int(now) - 3 * 24 * 3600)
            directory = snapshot_dir(ctx.cfg)
            if directory is not None:
                payloads = build_payloads(ctx.db, ctx.cfg)
                write_payloads(directory, payloads)
                book = book_value(ctx.db)
                ctx.db.record_balance(book["equity"], book["cash"], ctx.db.get_setting("mode"))
                ctx.db.heartbeat("writer", "ok", "snapshot")
                interval = max(15, int(ctx.cfg.pages_push_sec or 60))
                if ctx.cfg.pages_push and now - last_push >= interval:
                    rel = str(Path(ctx.cfg.pages_dir) / "d" / ctx.cfg.data_path)
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(maybe_push, ctx.cfg, rel),
                            timeout=25,
                        )
                    except asyncio.TimeoutError:
                        log.warning("pages push timed out")
                    last_push = now
                    ctx.db.heartbeat("writer", "ok", "snapshot")
            else:
                ctx.db.heartbeat("writer", "disabled", "DATA_PATH unset")
        except SnapshotRefused:
            log.warning("snapshot refused")
            ctx.db.heartbeat("writer", "error", "refused")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("writer failed: %s", type(exc).__name__)
            ctx.db.heartbeat("writer", "error", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
