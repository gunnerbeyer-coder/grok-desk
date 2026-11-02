"""Rules analyst. No model calls. Alive, organic, flush, chase, then a score gate."""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("desk.analyst")


def _f(value, default=0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _maybe(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def score_rules(alive: bool, organic: bool, liq: float, volume: float, h1) -> float:
    score = 0.0
    if alive:
        score += 30
    if organic:
        score += 25
    score += max(0.0, min(20.0, (liq - 40_000) / 5_000))
    score += max(0.0, min(15.0, volume / 1_000))
    if h1 is not None and 0 <= h1 <= 25:
        score += 10
    elif h1 is not None and h1 < 0:
        score += 4
    return round(min(100.0, score), 2)


def evaluate(metrics: dict, settings: dict) -> dict:
    """Gate is true only when the coin is alive, organic, not flushing, not a chase, and scored."""
    if settings.get("analyst_mode") not in (None, "", "rules"):
        return {
            "alive": False,
            "organic": False,
            "flush": False,
            "chase": False,
            "score": 0,
            "gate": False,
            "reason": "analyst_mode",
        }
    buys = _f(metrics.get("buys_m5"))
    sells = _f(metrics.get("sells_m5"))
    volume = _f(metrics.get("volume_m5"))
    liq = _f(metrics.get("liquidity_usd"))
    m5 = _maybe(metrics.get("price_change_m5"))
    h1 = _maybe(metrics.get("price_change_h1"))
    txns = buys + sells
    alive = txns >= float(settings["alive_min_txns_m5"]) and volume >= float(settings["alive_min_volume_m5"])
    ratio = (buys / txns) if txns else 0.0
    organic = (
        sells >= 1
        and float(settings["organic_min_buy_ratio"]) <= ratio <= float(settings["organic_max_buy_ratio"])
    )
    flush = m5 is not None and m5 <= float(settings["flush_m5_pct"])
    chase = h1 is not None and h1 >= float(settings["chase_h1_pct"])
    score = score_rules(alive, organic, liq, volume, h1)
    reasons = []
    if not alive:
        reasons.append("dead")
    if not organic:
        reasons.append("not_organic")
    if flush:
        reasons.append("flush")
    if chase:
        reasons.append("chase")
    if score < float(settings["min_score"]):
        reasons.append("score")
    gate = not reasons
    return {
        "alive": alive,
        "organic": organic,
        "flush": flush,
        "chase": chase,
        "score": score,
        "gate": gate,
        "reason": "pass" if gate else ",".join(reasons),
    }


async def run(ctx, stop: asyncio.Event) -> None:
    queue = ctx.bus.subscribe("audit_pass")
    await asyncio.sleep(0)
    while not stop.is_set():
        try:
            item = await asyncio.wait_for(queue.get(), timeout=5)
        except asyncio.TimeoutError:
            mode = ctx.db.get_setting("analyst_mode")
            ctx.db.heartbeat("analyst", "ok" if mode == "rules" else "error", mode or "rules")
            continue
        try:
            settings = ctx.db.settings_map()
            coin = ctx.db.coin(item["mint"])
            if coin and coin.get("status") in {"signal", "open", "awaiting", "skipped", "closed"}:
                continue
            if settings.get("analyst_mode") != "rules":
                ctx.db.heartbeat("analyst", "error", "analyst_mode must be rules")
                ctx.db.activity("verdict", "analyst is rules-only", item.get("mint"), {})
                continue
            verdict = evaluate(item.get("metrics") or {}, settings)
            mint = item["mint"]
            ctx.db.insert_verdict(mint=mint, raw=item.get("metrics") or {}, **verdict)
            symbol = item.get("symbol") or mint[:4]
            ctx.db.activity(
                "verdict",
                f"{symbol} {verdict['reason']} {verdict['score']}",
                mint,
                {"score": verdict["score"]},
            )
            if verdict["gate"]:
                price = _maybe((item.get("metrics") or {}).get("price_usd"))
                if price is None or price <= 0:
                    ctx.db.upsert_coin(mint, status="rejected", kill_reason="no_price")
                    ctx.db.skip(mint, "no_price", "analyst")
                else:
                    mode = settings.get("mode") or "paper"
                    signal_id = ctx.db.insert_signal(mint, price, verdict["score"], mode)
                    ctx.db.upsert_coin(mint, status="signal")
                    ctx.bus.publish(
                        "signal",
                        {
                            "signal_id": signal_id,
                            "mint": mint,
                            "symbol": item.get("symbol"),
                            "name": item.get("name"),
                            "price": price,
                            "score": verdict["score"],
                            "metrics": item.get("metrics") or {},
                        },
                    )
            else:
                ctx.db.upsert_coin(mint, status="watch", kill_reason=verdict["reason"])
            ctx.db.heartbeat("analyst", "ok", f"{symbol} {verdict['reason']}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("analyst failed: %s", type(exc).__name__)
            ctx.db.heartbeat("analyst", "error", type(exc).__name__)
