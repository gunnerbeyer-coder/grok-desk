"""Size, recheck, and record the local book.

Step 11: paper fills at the signal price. Live waits for the owner to tap
Bought. Neither mode places an order or touches a wallet.
"""

from __future__ import annotations

import asyncio
import logging
import time

from desk.agents.scanner import as_float, normalize_pair, pumpswap_pair
from desk.fees import fomo_solana_fee
from desk.links import coin_url, require_canonical

log = logging.getLogger("desk.executor")

PAPER_PREFIX = "PAPER · "


def paper_fill_price(signal_price: float) -> float:
    """Paper fills print at the signal price. No slippage model, no broker."""
    return float(signal_price)


def fit_size(cash: float, requested: float, ref_discount: float, min_position: float) -> tuple[float, float]:
    """Notional plus the FOMO entry fee has to fit in cash."""
    size = min(float(requested), float(cash))
    if size <= 0:
        return 0.0, 0.0
    fee = fomo_solana_fee(size, ref_discount)
    for _ in range(4):
        if size + fee <= cash + 1e-9:
            break
        size = max(0.0, cash - fee)
        fee = fomo_solana_fee(size, ref_discount)
        if size + fee > cash and size > 0:
            size = size * cash / (size + fee)
            fee = fomo_solana_fee(size, ref_discount)
    if size < min_position:
        return 0.0, 0.0
    if size + fee > cash + 1e-6:
        return 0.0, 0.0
    return size, fee


def plan_entry(
    *,
    mode: str,
    paused: bool,
    price: float | None,
    equity: float,
    cash: float,
    open_count: int,
    recheck_ok: bool,
    recheck_reason: str,
    settings: dict,
    mint: str,
) -> dict:
    url = require_canonical(coin_url(mint))
    base = {
        "coin_url": url,
        "signal_price": price,
        "fill_price": None,
        "size_usd": 0.0,
        "qty": 0.0,
        "entry_fee": 0.0,
        "stop": None,
        "action": "skip",
        "reason": "",
    }
    if paused:
        base["reason"] = "paused"
        return base
    if mode not in {"paper", "live"}:
        base["reason"] = "mode"
        return base
    if not recheck_ok:
        base["reason"] = recheck_reason or "recheck"
        return base
    if price is None or price <= 0:
        base["reason"] = "no_price"
        return base
    if open_count >= int(float(settings["max_open_positions"])):
        base["reason"] = "capacity"
        return base
    stop_pct = float(settings["stop_pct"])
    if stop_pct <= 0:
        base["reason"] = "stop"
        return base
    risk_usd = float(equity) * float(settings["risk_per_trade_pct"]) / 100.0
    requested = risk_usd / (stop_pct / 100.0)
    requested = min(requested, float(settings["max_position_usd"]))
    discount = float(settings["fees_ref_discount"])
    size, fee = fit_size(float(cash), requested, discount, float(settings["min_position_usd"]))
    if size <= 0:
        base["reason"] = "size"
        return base
    fill = paper_fill_price(price) if mode == "paper" else None
    base.update(
        {
            "action": "fill" if mode == "paper" else "await",
            "reason": "paper" if mode == "paper" else "awaiting_user",
            "size_usd": size,
            "qty": size / paper_fill_price(price),
            "entry_fee": fee,
            "stop": paper_fill_price(price) * (1.0 - stop_pct / 100.0),
            "fill_price": fill,
            "signal_price": price,
        }
    )
    return base


def signal_text(mode: str, symbol: str, price: float, size: float, stop: float) -> str:
    body = f"BUY {symbol}\n{price:.8g} · ${size:.2f}\nstop {stop:.8g}"
    if mode == "paper":
        return PAPER_PREFIX + body
    return body


def sell_text(mode: str, symbol: str, price: float, reason: str, pnl: float) -> str:
    body = f"SELL {symbol}\n{reason} · {price:.8g}\npnl {pnl:+.2f}"
    if mode == "paper":
        return PAPER_PREFIX + body
    return body


def recheck_metrics(metrics: dict | None, settings: dict) -> tuple[bool, str]:
    if not metrics:
        return False, "recheck"
    liq = as_float(metrics.get("liquidity_usd"))
    h1 = as_float(metrics.get("price_change_h1"))
    if liq is None or liq < float(settings["min_liq_usd"]):
        return False, "liq"
    if h1 is not None and h1 > float(settings["max_h1_pct"]):
        return False, "h1"
    return True, "ok"


def _buttons(mint: str, signal_id: int) -> list:
    url = require_canonical(coin_url(mint))
    return [
        [{"text": "Buy on FOMO", "url": url}],
        [
            {"text": "Bought", "callback_data": f"bought:{signal_id}"},
            {"text": "Skip", "callback_data": f"skip:{signal_id}"},
        ],
    ]


def sell_buttons(mint: str) -> list:
    url = require_canonical(coin_url(mint))
    return [[{"text": "Sell on FOMO", "url": url}]]


def _equity(db) -> tuple[float, float]:
    cash = float(db.get_setting("cash") or 0)
    mtm = 0.0
    for pos in db.open_positions():
        last = db.latest_price(pos["mint"])
        px = float(last["price_usd"]) if last and last["price_usd"] is not None else float(pos["entry_price"])
        mtm += float(pos["qty"]) * px
    return cash, cash + mtm


def _notify(ctx, text: str, buttons: list | None, mint: str) -> None:
    ctx.bus.publish("tg", {"text": text, "buttons": buttons or [], "mint": mint})


def open_from_plan(ctx, signal_id: int, plan: dict, mode: str) -> int | None:
    timeout = int(float(ctx.db.get_setting("timeout_sec")))
    deadline = int(time.time()) + timeout
    position_id = ctx.db.fill_new_signal(signal_id, plan, mode, deadline)
    if position_id is None:
        return None
    sig = ctx.db.signal(signal_id)
    mint = sig["mint"] if sig else ""
    ctx.db.activity(
        "fill",
        f"{mode} fill {plan['fill_price']}",
        mint,
        {"size": plan["size_usd"]},
    )
    return position_id


def close_book(ctx, position: dict, exit_price: float, reason: str) -> dict | None:
    discount = float(ctx.db.get_setting("fees_ref_discount"))
    proceeds = float(position["qty"]) * float(exit_price)
    exit_fee = fomo_solana_fee(proceeds, discount)
    pnl = (proceeds - exit_fee) - (float(position["size_usd"]) + float(position["entry_fee"]))
    ok = ctx.db.close_position(position["id"], float(exit_price), exit_fee, reason, pnl)
    if not ok:
        return None
    ctx.db.activity(
        "exit",
        f"{reason} pnl {pnl:+.2f}",
        position["mint"],
        {"pnl": round(pnl, 4)},
    )
    return {"pnl": pnl, "exit_fee": exit_fee, "exit_price": float(exit_price)}


def confirm_bought(ctx, signal_id: int) -> str:
    sig = ctx.db.signal(signal_id)
    if sig is None:
        return "Unknown signal."
    if sig["status"] == "filled":
        return "Already filled at the signal price."
    if sig["status"] != "awaiting":
        return "That signal is closed."
    price = float(sig["price"])
    settings = ctx.db.settings_map()
    cash, equity = _equity(ctx.db)
    plan = plan_entry(
        mode="paper",
        paused=False,
        price=price,
        equity=equity,
        cash=cash,
        open_count=ctx.db.open_count(),
        recheck_ok=True,
        recheck_reason="ok",
        settings=settings,
        mint=sig["mint"],
    )
    # The owner confirmed a live buy. Record it locally at the signal price.
    # plan_entry is asked for paper math so the fill price is the signal price.
    if plan["action"] != "fill":
        ctx.db.skip(sig["mint"], plan["reason"], "bought")
        return f"Not recorded ({plan['reason']})."
    plan["action"] = "fill"
    plan["reason"] = "bought"
    position_id = open_from_plan(ctx, signal_id, plan, "live")
    if position_id is None:
        return "Already handled."
    return "Recorded locally at the signal price. No order was sent."


def confirm_skip(ctx, signal_id: int) -> str:
    sig = ctx.db.signal(signal_id)
    if sig is None:
        return "Unknown signal."
    if sig["status"] == "filled":
        return "Already filled. Use /sell to close the local position."
    if ctx.db.mark_skipped(signal_id):
        ctx.db.skip(sig["mint"], "skip", "button")
        ctx.db.upsert_coin(sig["mint"], status="skipped", kill_reason="skip")
        ctx.db.activity("skip", "skipped", sig["mint"], {})
        return "Skipped."
    return "Already handled."


async def _fresh_metrics(ctx, mint: str) -> dict | None:
    pairs = await ctx.vendors.dexscreener([mint])
    chosen = pumpswap_pair(pairs)
    if not chosen:
        return None
    return {
        "liquidity_usd": as_float(chosen.get("liquidity_usd")),
        "price_change_h1": as_float(chosen.get("priceChange_h1")),
        "price_usd": as_float(chosen.get("priceUsd")),
    }


async def handle_signal(ctx, item: dict) -> None:
    settings = ctx.db.settings_map()
    mode = settings.get("mode") or "paper"
    paused = settings.get("paused") == "1"
    mint = item["mint"]
    symbol = item.get("symbol") or mint[:4]
    # The fill prints at the signal price. A fresh quote can only veto the trade.
    if item.get("manual"):
        re_ok, re_reason = True, "manual"
    else:
        fresh = await _fresh_metrics(ctx, mint)
        re_ok, re_reason = recheck_metrics(fresh, settings)
    price = item.get("price")
    cash, equity = _equity(ctx.db)
    plan = plan_entry(
        mode=mode,
        paused=paused,
        price=float(price) if price else None,
        equity=equity,
        cash=cash,
        open_count=ctx.db.open_count(),
        recheck_ok=re_ok,
        recheck_reason=re_reason,
        settings=settings,
        mint=mint,
    )
    signal_id = int(item["signal_id"])
    if plan["action"] == "skip":
        ctx.db.mark_skipped(signal_id)
        ctx.db.skip(mint, plan["reason"], "executor")
        ctx.db.upsert_coin(mint, status="skipped", kill_reason=plan["reason"])
        ctx.db.activity("skip", f"skip {plan['reason']}", mint, {})
        ctx.db.heartbeat("executor", "ok", f"skip {plan['reason']}")
        return
    if plan["action"] == "fill":
        position_id = open_from_plan(ctx, signal_id, plan, "paper")
        if position_id is None:
            return
        text = signal_text("paper", symbol, plan["fill_price"], plan["size_usd"], plan["stop"])
        _notify(ctx, text, _buttons(mint, signal_id), mint)
        ctx.db.heartbeat("executor", "ok", f"paper {symbol}")
        return
    if plan["action"] == "await":
        if not ctx.db.mark_awaiting(signal_id, plan["size_usd"]):
            return
        ctx.db.upsert_coin(mint, status="awaiting")
        text = signal_text("live", symbol, plan["signal_price"], plan["size_usd"], plan["stop"])
        _notify(ctx, text, _buttons(mint, signal_id), mint)
        ctx.db.activity("signal", f"awaiting {symbol}", mint, {})
        ctx.db.heartbeat("executor", "ok", f"await {symbol}")


async def run(ctx, stop: asyncio.Event) -> None:
    queue = ctx.bus.subscribe("signal")
    await asyncio.sleep(0)
    while not stop.is_set():
        try:
            item = await asyncio.wait_for(queue.get(), timeout=5)
        except asyncio.TimeoutError:
            ctx.db.heartbeat("executor", "ok", ctx.db.get_setting("mode"))
            continue
        try:
            await handle_signal(ctx, item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("executor failed: %s", type(exc).__name__)
            ctx.db.heartbeat("executor", "error", type(exc).__name__)
