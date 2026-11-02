"""Migration scanner.

PumpPortal subscribeMigration only. `pool` on that message is a type string.
The pool address is the DexScreener pairAddress, and only dexId pumpswap stays.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import websockets

from desk.fields import DEXSCREENER, PUMPPORTAL_MIGRATION, dig
from desk.vendors import PUMPPORTAL_WS

log = logging.getLogger("desk.scanner")

KILL_ORDER = ("liq", "top10", "h1", "copycat")
_inflight: set[str] = set()
_last_price_write: dict[str, float] = {}

PROTECTED = {
    "SOL",
    "BTC",
    "ETH",
    "USDC",
    "USDT",
    "BONK",
    "WIF",
    "JUP",
    "RAY",
    "JTO",
    "POPCAT",
    "FARTCOIN",
    "TRUMP",
    "MELANIA",
}


def parse_migration(msg: dict) -> dict | None:
    """Pull the verified PumpPortal keys. `pool` is stored as a type, never an address."""
    if not isinstance(msg, dict):
        return None
    mint_key = PUMPPORTAL_MIGRATION["mint"]
    mint = msg.get(mint_key) if mint_key else None
    if not mint or not isinstance(mint, str):
        return None
    pool_key = PUMPPORTAL_MIGRATION["pool"]
    sig_key = PUMPPORTAL_MIGRATION["signature"]
    tx_key = PUMPPORTAL_MIGRATION["txType"]
    return {
        "mint": mint,
        "signature": msg.get(sig_key) if sig_key else None,
        "tx_type": msg.get(tx_key) if tx_key else None,
        "pool_type": msg.get(pool_key) if pool_key else None,
    }


def normalize_pair(pair: dict) -> dict:
    out = {}
    for name, path in DEXSCREENER.items():
        out[name] = None if path is None else dig(pair, path)
    return out


def as_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pumpswap_pair(pairs: list[dict]) -> dict | None:
    best = None
    best_liq = -1.0
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        norm = normalize_pair(pair)
        dex = str(norm.get("dexId") or "").lower()
        if dex != "pumpswap":
            continue
        liq = as_float(norm.get("liquidity_usd")) or 0.0
        if liq >= best_liq:
            best = norm
            best_liq = liq
    return best


def _alnum(value: str | None) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def is_copycat(symbol: str | None, name: str | None, taken: set[str]) -> bool:
    sym = _alnum(symbol)
    label = _alnum(name)
    if sym in taken:
        return True
    for protected in PROTECTED:
        if sym == protected or label == protected:
            return True
        if sym.startswith(protected) and len(sym) <= len(protected) + 2:
            return True
    return False


def age_seconds(pair_created_at, now_s: float):
    created = as_float(pair_created_at)
    if created is None:
        return None
    if created > 10_000_000_000:
        created = created / 1000.0
    return now_s - created


def screen_decision(screen: dict, settings: dict, waited_s: float) -> str:
    """First matching kill in liq / top10 / h1 / copycat order, else wait or pass.

    Missing liq or top10 waits until waiting_room_max_sec, then kill:incomplete.
    Age under min_age_sec waits, then kill:stale. Present values use strict
    inequalities from Step 10: liq < floor, top10 > cap, h1 > cap.
    """
    min_liq = float(settings["min_liq_usd"])
    max_top = float(settings["max_top10_pct"])
    max_h1 = float(settings["max_h1_pct"])
    min_age = float(settings["min_age_sec"])
    max_wait = float(settings["waiting_room_max_sec"])

    liq = screen.get("liq_usd")
    top10 = screen.get("top10_pct")
    h1 = screen.get("h1_pct")
    if liq is not None and float(liq) < min_liq:
        return "kill:liq"
    if top10 is not None and float(top10) > max_top:
        return "kill:top10"
    if h1 is not None and float(h1) > max_h1:
        return "kill:h1"
    if screen.get("copycat"):
        return "kill:copycat"
    if liq is None or top10 is None:
        if waited_s >= max_wait:
            return "kill:incomplete"
        return "wait"
    age = screen.get("age_s")
    if age is None or float(age) < min_age:
        if waited_s >= max_wait:
            return "kill:stale"
        return "wait"
    return "pass"


def top10_pct(holders: list[dict], pool_ids: set[str]) -> float | None:
    """Sum of the largest ten holder pct values, excluding the pool vault.

    A holder whose owner or address equals a market pubkey is the vault.
    pct values that are all <= 1 are treated as fractions.
    """
    pcts = []
    for holder in holders or []:
        if not isinstance(holder, dict):
            continue
        owner = holder.get("owner")
        address = holder.get("address")
        if owner in pool_ids or address in pool_ids:
            continue
        raw = holder.get("pct")
        if isinstance(raw, (int, float)):
            pcts.append(float(raw))
    if not pcts:
        return None
    if max(pcts) <= 1:
        pcts = [p * 100.0 for p in pcts]
    pcts.sort(reverse=True)
    return sum(pcts[:10])


def _num_settings(db) -> dict:
    return db.settings_map()


async def _price_waiting(ctx, mints: list[str], last_write: dict) -> None:
    now = time.time()
    due = [m for m in mints if now - last_write.get(m, 0) >= 60]
    if not due:
        return
    pairs = await ctx.vendors.dexscreener(due)
    by_mint: dict[str, dict] = {}
    for pair in pairs:
        norm = normalize_pair(pair)
        mint = norm.get("baseMint")
        if mint:
            by_mint.setdefault(mint, norm)
    for mint in due:
        norm = by_mint.get(mint)
        if not norm:
            continue
        price = as_float(norm.get("priceUsd"))
        liq = as_float(norm.get("liquidity_usd"))
        ctx.db.insert_price(mint, price, liq, "scanner")
        last_write[mint] = now


def _note_price(ctx, mint: str, price, liq, force: bool = False) -> None:
    now = time.time()
    if not force and now - _last_price_write.get(mint, 0) < 60:
        return
    if price is None and liq is None:
        return
    ctx.db.insert_price(mint, price, liq, "scanner")
    _last_price_write[mint] = now


async def _evaluate(ctx, mint: str, pool_type: str, waited_s: float) -> None:
    if mint in _inflight:
        return
    _inflight.add(mint)
    try:
        await _evaluate_body(ctx, mint, pool_type, waited_s)
    finally:
        _inflight.discard(mint)


async def _evaluate_body(ctx, mint: str, pool_type: str, waited_s: float) -> None:
    pairs = await ctx.vendors.dexscreener([mint])
    chosen = pumpswap_pair(pairs)
    if chosen is None:
        if waited_s >= float(ctx.db.get_setting("waiting_room_max_sec")):
            ctx.db.upsert_coin(mint, status="killed", kill_reason="no_pumpswap", pool_type=pool_type)
            ctx.db.insert_scan(mint=mint, passed=False, reason="no_pumpswap")
            ctx.db.activity("kill", f"{mint[:4]}… no pumpswap pair", mint, {"reason": "no_pumpswap"})
        return
    report = await ctx.vendors.rug_report(mint)
    holders = []
    pool_ids = set()
    pair_address = chosen.get("pairAddress")
    if pair_address:
        pool_ids.add(pair_address)
    if isinstance(report, dict):
        markets = report.get("markets") if isinstance(report.get("markets"), list) else []
        for market in markets:
            if isinstance(market, dict) and market.get("pubkey"):
                pool_ids.add(market["pubkey"])
        raw_holders = report.get("topHolders")
        if isinstance(raw_holders, list):
            holders = raw_holders
    top = top10_pct(holders, pool_ids) if holders else None
    symbol = chosen.get("symbol")
    name = chosen.get("name")
    taken = ctx.db.symbols_taken(exclude_mint=mint)
    copycat = is_copycat(symbol, name, taken)
    now = time.time()
    screen = {
        "liq_usd": as_float(chosen.get("liquidity_usd")),
        "top10_pct": top,
        "h1_pct": as_float(chosen.get("priceChange_h1")),
        "copycat": copycat,
        "age_s": age_seconds(chosen.get("pairCreatedAt"), now),
    }
    decision = screen_decision(screen, _num_settings(ctx.db), waited_s)
    price = as_float(chosen.get("priceUsd"))
    ctx.db.insert_scan(
        mint=mint,
        liq_usd=screen["liq_usd"],
        top10_pct=screen["top10_pct"],
        price_change_h1=screen["h1_pct"],
        copycat=copycat,
        age_s=None if screen["age_s"] is None else int(screen["age_s"]),
        passed=decision == "pass",
        reason=decision,
        price_usd=price,
        raw={"symbol": symbol, "pair": pair_address, "pool_type": pool_type},
    )
    ctx.db.upsert_coin(
        mint,
        name=name,
        symbol=symbol,
        pair_address=pair_address,
        dex_id="pumpswap",
        pool_type=pool_type,
        pair_created_at=None if as_float(chosen.get("pairCreatedAt")) is None else int(as_float(chosen.get("pairCreatedAt"))),
        status="audit" if decision == "pass" else ("waiting" if decision == "wait" else "killed"),
        kill_reason=None if decision in {"pass", "wait"} else decision.split(":", 1)[-1],
    )
    _note_price(ctx, mint, price, screen["liq_usd"], force=decision == "pass")
    if decision == "pass":
        ctx.db.activity("scan", f"passed {symbol or mint[:4]}", mint, {"reason": "pass"})
        ctx.bus.publish(
            "scan_pass",
            {
                "mint": mint,
                "symbol": symbol,
                "name": name,
                "pair_address": pair_address,
                "pool_type": pool_type,
                "metrics": {
                    "liquidity_usd": screen["liq_usd"],
                    "volume_m5": as_float(chosen.get("volume_m5")),
                    "buys_m5": as_float(chosen.get("buys_m5")),
                    "sells_m5": as_float(chosen.get("sells_m5")),
                    "price_change_m5": as_float(chosen.get("priceChange_m5")),
                    "price_change_h1": as_float(chosen.get("priceChange_h1")),
                    "price_usd": price,
                },
            },
        )
    elif decision.startswith("kill"):
        ctx.db.activity("kill", f"{symbol or mint[:4]} {decision}", mint, {"reason": decision})


# Reconnect bounds. A dead TCP session can sit in retransmit for many minutes;
# these caps are what keep that off the rest of the desk.
OPEN_TIMEOUT = 10
RECV_TIMEOUT = 20
CLOSE_TIMEOUT = 2
WATCHDOG_AFTER = 45
EVAL_TIMEOUT = 20


class LiveSocket:
    """The socket the watchdog is allowed to abort from another task."""

    def __init__(self) -> None:
        self.ws = None
        self.force = asyncio.Event()
        self.touched = time.monotonic()

    def touch(self) -> None:
        self.touched = time.monotonic()

    def abort(self) -> None:
        self.force.set()
        ws = self.ws
        transport = getattr(ws, "transport", None) if ws is not None else None
        if transport is not None:
            try:
                transport.abort()
            except Exception:
                pass


async def close_socket(ws, timeout: float = CLOSE_TIMEOUT) -> None:
    """End a socket without waiting out a dead peer.

    `async with websockets.connect()` calls close(), which shields the
    "connection lost" wait. On ConnectionClosedError that wait is the dead
    TCP session. Abort the transport if the handshake does not finish.
    """
    if ws is None:
        return
    transport = getattr(ws, "transport", None)
    try:
        await asyncio.wait_for(ws.close(), timeout=timeout)
    except Exception:
        pass
    if transport is not None:
        try:
            transport.abort()
        except Exception:
            pass


async def interruptible_sleep(seconds: float, stop: asyncio.Event, live: LiveSocket) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop.is_set() or live.force.is_set():
            return
        await asyncio.sleep(min(1.0, deadline - time.monotonic()))


async def open_pumpportal():
    # proxy=None skips urllib.getproxies(), which is a blocking DNS lookup
    # inside the async connect path and ignores open_timeout.
    return await websockets.connect(
        PUMPPORTAL_WS,
        proxy=None,
        open_timeout=OPEN_TIMEOUT,
        close_timeout=CLOSE_TIMEOUT,
        ping_interval=20,
        ping_timeout=10,
        max_size=1_000_000,
    )


def scanner_is_stale(live: LiveSocket, now: float | None = None, after: float = WATCHDOG_AFTER) -> bool:
    clock = time.monotonic() if now is None else now
    return clock - live.touched > after


async def watchdog_step(ctx, live: LiveSocket, after: float = WATCHDOG_AFTER) -> bool:
    """Force a reconnect when the socket itself has gone quiet."""
    if not scanner_is_stale(live, after=after):
        return False
    log.warning("scanner watchdog reconnect")
    ctx.db.heartbeat("scanner", "error", "watchdog")
    live.abort()
    return True


async def run(ctx, stop: asyncio.Event) -> None:
    queue: asyncio.Queue = asyncio.Queue()
    last_price: dict[str, float] = {}
    live = LiveSocket()
    await asyncio.gather(
        _socket_loop(ctx, queue, stop, live),
        _batch_loop(ctx, queue, stop),
        _waiting_loop(ctx, last_price, stop),
        _watchdog(ctx, live, stop),
    )


async def _watchdog(ctx, live: LiveSocket, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
            return
        except asyncio.TimeoutError:
            pass
        try:
            await watchdog_step(ctx, live)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("scanner watchdog: %s", type(exc).__name__)


async def _socket_loop(ctx, queue: asyncio.Queue, stop: asyncio.Event, live: LiveSocket, connect=open_pumpportal) -> None:
    backoff = 2.0
    while not stop.is_set():
        if live.force.is_set():
            live.force.clear()
        ws = None
        failed = False
        try:
            live.touch()
            ctx.db.heartbeat("scanner", "ok", "connecting")
            ws = await asyncio.wait_for(connect(), timeout=OPEN_TIMEOUT + 2)
            live.ws = ws
            await asyncio.wait_for(ws.send(json.dumps({"method": "subscribeMigration"})), timeout=10)
            live.touch()
            ctx.db.heartbeat("scanner", "ok", "subscribed migrations")
            backoff = 2.0
            while not stop.is_set() and not live.force.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
                except asyncio.TimeoutError:
                    live.touch()
                    ctx.db.heartbeat("scanner", "ok", "listening")
                    continue
                live.touch()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                messages = payload if isinstance(payload, list) else [payload]
                for msg in messages:
                    parsed = parse_migration(msg)
                    if parsed:
                        await queue.put(parsed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed = True
            log.warning("pumpportal reconnect: %s", type(exc).__name__)
            ctx.db.heartbeat("scanner", "error", type(exc).__name__)
            live.touch()
        finally:
            live.ws = None
            await close_socket(ws)
        if stop.is_set():
            return
        if failed or live.force.is_set():
            await interruptible_sleep(backoff, stop, live)
            backoff = min(30.0, backoff * 2)


async def _batch_loop(ctx, queue: asyncio.Queue, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            item = await asyncio.wait_for(queue.get(), timeout=5)
        except asyncio.TimeoutError:
            continue
        batch = [item]
        while len(batch) < 20:
            try:
                batch.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for parsed in batch:
            mint = parsed["mint"]
            if ctx.db.coin(mint):
                continue
            ctx.db.upsert_coin(
                mint,
                pool_type=parsed.get("pool_type"),
                status="waiting",
                first_seen=int(time.time()),
            )
            try:
                await asyncio.wait_for(
                    _evaluate(ctx, mint, parsed.get("pool_type") or "", 0),
                    timeout=EVAL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                _inflight.discard(mint)
                log.warning("scan evaluate timed out")
                ctx.db.heartbeat("scanner", "error", "evaluate timeout")
            except Exception as exc:
                log.warning("scan evaluate failed: %s", type(exc).__name__)
                ctx.db.heartbeat("scanner", "error", type(exc).__name__)


async def _waiting_loop(ctx, last_price: dict, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            waiting = ctx.db.coins_by_status(("waiting",))
            ctx.db.heartbeat("scanner", "ok", f"waiting {len(waiting)}")
            mints = [c["mint"] for c in waiting[:8]]
            await asyncio.wait_for(_price_waiting(ctx, mints, last_price), timeout=EVAL_TIMEOUT)
            now = time.time()
            for coin in waiting[:8]:
                waited = now - float(coin.get("first_seen") or now)
                try:
                    await asyncio.wait_for(
                        _evaluate(ctx, coin["mint"], coin.get("pool_type") or "", waited),
                        timeout=EVAL_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    _inflight.discard(coin["mint"])
                    log.warning("waiting recheck timed out")
                except Exception as exc:
                    log.warning("waiting recheck failed: %s", type(exc).__name__)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("waiting loop: %s", type(exc).__name__)
            ctx.db.heartbeat("scanner", "error", type(exc).__name__)
        await asyncio.sleep(15)
