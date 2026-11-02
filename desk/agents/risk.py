"""Poll open positions. Stops only ratchet tighter. Exits are local sells."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from desk.agents.executor import close_book, sell_buttons, sell_text
from desk.agents.scanner import as_float, normalize_pair

log = logging.getLogger("desk.risk")


@dataclass
class Advance:
    stop: float
    high: float
    exit_reason: str | None
    exit_price: float | None


def advance_position(
    *,
    entry: float,
    stop: float,
    high: float,
    opened_at: int,
    deadline: int,
    price: float | None,
    now: int,
    timeout_sec: int,
    trail_pct: float,
    trail_arm_pct: float,
) -> Advance:
    """Move the stop up only. A wider trail setting cannot loosen an open stop.

    A shorter timeout can bring the deadline forward. A longer one cannot
    extend the deadline stored on the position.
    """
    new_high = high
    if price is not None:
        new_high = max(high, price, entry)
    new_stop = stop
    if price is not None and entry > 0:
        gain_pct = (new_high - entry) / entry * 100.0
        if gain_pct >= trail_arm_pct:
            candidate = new_high * (1.0 - trail_pct / 100.0)
            if candidate > new_stop:
                new_stop = candidate
    if new_stop < stop:
        new_stop = stop
    if new_high < high:
        new_high = high
    effective_deadline = min(int(deadline), int(opened_at) + int(timeout_sec))
    reason = None
    exit_price = None
    if price is not None and price <= new_stop:
        reason = "stop"
        exit_price = price
    elif now >= effective_deadline:
        reason = "timeout"
        exit_price = price if price is not None else entry
    return Advance(new_stop, new_high, reason, exit_price)


async def _prices(ctx, mints: list[str]) -> dict[str, float]:
    if not mints:
        return {}
    pairs = await ctx.vendors.dexscreener(mints)
    found: dict[str, tuple[float | None, float | None]] = {}
    for pair in pairs:
        norm = normalize_pair(pair)
        mint = norm.get("baseMint")
        if not mint:
            continue
        price = as_float(norm.get("priceUsd"))
        liq = as_float(norm.get("liquidity_usd"))
        prev = found.get(mint)
        if prev is None or (liq or 0) >= (prev[1] or 0):
            found[mint] = (price, liq)
    out = {}
    for mint, (price, liq) in found.items():
        if price is not None:
            ctx.db.insert_price(mint, price, liq, "risk")
            out[mint] = price
    return out


def _symbol(ctx, mint: str) -> str:
    coin = ctx.db.coin(mint)
    if coin and coin.get("symbol"):
        return coin["symbol"]
    return mint[:4]


async def run(ctx, stop: asyncio.Event) -> None:
    await asyncio.sleep(0)
    while not stop.is_set():
        try:
            positions = ctx.db.open_positions()
            prices = await _prices(ctx, [p["mint"] for p in positions])
            settings = ctx.db.settings_map()
            timeout_sec = int(float(settings["timeout_sec"]))
            trail_pct = float(settings["trail_pct"])
            arm = float(settings["trail_arm_pct"])
            now = int(time.time())
            for pos in positions:
                # A missed poll must not reuse an older print and trip a ratcheted stop.
                price = prices.get(pos["mint"])
                adv = advance_position(
                    entry=float(pos["entry_price"]),
                    stop=float(pos["stop"]),
                    high=float(pos["trail_high"]),
                    opened_at=int(pos["opened_at"]),
                    deadline=int(pos["deadline"]),
                    price=price,
                    now=now,
                    timeout_sec=timeout_sec,
                    trail_pct=trail_pct,
                    trail_arm_pct=arm,
                )
                ctx.db.update_stop(pos["id"], adv.stop, adv.high)
                exit_price = adv.exit_price
                if adv.exit_reason == "timeout" and price is None:
                    last = ctx.db.latest_price(pos["mint"])
                    if last and last.get("price_usd") is not None:
                        exit_price = float(last["price_usd"])
                if adv.exit_reason and exit_price is not None:
                    # Re-read so a concurrent close is harmless, and so the
                    # stop we persist is the one already ratcheted.
                    current = ctx.db.position_for_mint(pos["mint"])
                    if current is None:
                        continue
                    result = close_book(ctx, current, exit_price, adv.exit_reason)
                    if result is None:
                        continue
                    symbol = _symbol(ctx, pos["mint"])
                    text = sell_text(
                        current["mode"],
                        symbol,
                        result["exit_price"],
                        adv.exit_reason,
                        result["pnl"],
                    )
                    ctx.bus.publish(
                        "tg",
                        {
                            "text": text,
                            "buttons": sell_buttons(pos["mint"]),
                            "mint": pos["mint"],
                        },
                    )
            ctx.db.heartbeat("risk", "ok", f"open {len(positions)}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("risk failed: %s", type(exc).__name__)
            ctx.db.heartbeat("risk", "error", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
