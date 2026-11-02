"""Text report for Telegram /report and the JSON balance block."""

from __future__ import annotations

from desk.db import DB


def book_value(db: DB) -> dict:
    cash = float(db.get_setting("cash") or 0)
    starting = float(db.get_setting("starting_equity") or cash)
    rows = []
    marked = 0.0
    for pos in db.open_positions():
        last = db.latest_price(pos["mint"])
        price = float(last["price_usd"]) if last and last["price_usd"] is not None else float(pos["entry_price"])
        value = float(pos["qty"]) * price
        marked += value
        cost = float(pos["size_usd"]) + float(pos["entry_fee"])
        pnl = value - cost
        coin = db.coin(pos["mint"]) or {}
        rows.append(
            {
                "id": pos["id"],
                "mint": pos["mint"],
                "symbol": coin.get("symbol") or pos["mint"][:4],
                "name": coin.get("name") or "",
                "mode": pos["mode"],
                "entry": pos["entry_price"],
                "price": price,
                "stop": pos["stop"],
                "high": pos["trail_high"],
                "size_usd": pos["size_usd"],
                "qty": pos["qty"],
                "pnl_usd": pnl,
                "pnl_pct": (pnl / cost * 100.0) if cost else 0.0,
                "opened_at": pos["opened_at"],
                "deadline": pos["deadline"],
            }
        )
    equity = cash + marked
    return {
        "cash": cash,
        "equity": equity,
        "pnl": equity - starting,
        "starting": starting,
        "positions": rows,
    }


def build_report(db: DB) -> str:
    book = book_value(db)
    stats = db.closed_stats()
    settings = db.public_settings()
    lines = [
        "Desk report",
        f"mode {settings.get('mode')} · paused {settings.get('paused')}",
        f"equity ${book['equity']:.2f} · cash ${book['cash']:.2f} · pnl {book['pnl']:+.2f}",
        f"closed {stats['closed']} · wins {stats['wins']} · losses {stats['losses']} · realized {stats['realized']:+.2f}",
        f"analyst {settings.get('analyst_mode')} · notify {settings.get('notifier')} · handle {settings.get('fomo_handle')}",
    ]
    if book["positions"]:
        lines.append("open:")
        for pos in book["positions"]:
            lines.append(
                f"- {pos['symbol']} {pos['mode']} entry {pos['entry']:.6g} "
                f"now {pos['price']:.6g} stop {pos['stop']:.6g} pnl {pos['pnl_usd']:+.2f}"
            )
    else:
        lines.append("open: none")
    beats = {b["agent"]: b for b in db.heartbeats()}
    if beats:
        lines.append("agents:")
        for name in ("scanner", "auditor", "analyst", "executor", "risk", "telegram", "writer"):
            beat = beats.get(name)
            if not beat:
                continue
            lines.append(f"- {name} {beat['status']} {beat.get('detail') or ''}".rstrip())
    return "\n".join(lines)
