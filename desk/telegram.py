"""Telegram long poll. The first private /start becomes the owner.

Trade buttons carry a FOMO link from links.py. The desk never places the order.
"""

from __future__ import annotations

import asyncio
import logging

from desk.agents.executor import confirm_bought, confirm_skip, handle_signal, sell_buttons
from desk.agents.executor import close_book, sell_text
from desk.config import validate_setting
from desk.links import SIGNUP_URL, profile_url, require_canonical
from desk.report import book_value, build_report

log = logging.getLogger("desk.telegram")

API = "https://api.telegram.org"
HELP = (
    "Commands\n"
    "/buy <mint>\n/sell <mint>\n/why <mint>\n/pause\n/set <key> <value>\n"
    "/stack\n/status\n/paper\n/live\n/invite\n/report\n\n"
    "Paper fills locally at the signal price. Live records a position only after Bought. "
    "The desk does not place orders."
)


def parse_command(text: str | None):
    if not text:
        return None
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split()
    name = parts[0].split("@", 1)[0].lower()
    return name, parts[1:]


async def _call(ctx, method: str, body: dict) -> dict | None:
    token = ctx.cfg.tg_token
    if not token:
        return None
    try:
        resp = await ctx.http.post(f"{API}/bot{token}/{method}", json=body, timeout=35)
        data = resp.json()
        if not isinstance(data, dict) or not data.get("ok"):
            log.warning("telegram %s not ok", method)
            return None
        return data
    except Exception as exc:
        log.warning("telegram %s failed: %s", method, type(exc).__name__)
        return None


async def send(ctx, chat_id: str, text: str, buttons: list | None = None) -> None:
    if not chat_id or not text:
        return
    body = {"chat_id": chat_id, "text": text[:3900]}
    if buttons:
        body["reply_markup"] = {"inline_keyboard": buttons}
    await _call(ctx, "sendMessage", body)


def _owner(ctx) -> str:
    return ctx.db.get_setting("owner_chat_id")


def _allowed(ctx, chat_id: str) -> bool:
    owner = _owner(ctx)
    return bool(owner) and str(chat_id) == owner


async def _reply(ctx, chat_id: str, text: str) -> None:
    await send(ctx, chat_id, text)


async def on_command(ctx, chat_id: str, name: str, args: list[str]) -> None:
    if name == "/start":
        if ctx.db.claim_owner(str(chat_id)):
            await _reply(ctx, chat_id, "Desk online. You are the owner.\n" + HELP)
        else:
            return
        return
    if not _allowed(ctx, chat_id):
        return
    if name in {"/help", "/start"}:
        await _reply(ctx, chat_id, HELP)
        return
    if name == "/pause":
        current = ctx.db.get_setting("paused")
        nxt = "0" if current == "1" else "1"
        ctx.db.set_setting("paused", nxt, "pause")
        ctx.db.activity("pause", "paused" if nxt == "1" else "resumed", None, {})
        await _reply(ctx, chat_id, "Paused." if nxt == "1" else "Resumed. Open stops keep running.")
        return
    if name == "/paper":
        ctx.db.set_setting("mode", "paper", "paper")
        ctx.db.activity("mode", "paper", None, {})
        await _reply(ctx, chat_id, "Mode is paper. Fills stay on the local book.")
        return
    if name == "/live":
        ctx.db.set_setting("mode", "live", "live")
        ctx.db.activity("mode", "live", None, {})
        await _reply(
            ctx,
            chat_id,
            "Mode is live. A position is recorded only after you tap Bought. No order is sent.",
        )
        return
    if name == "/status":
        book = book_value(ctx.db)
        beats = {b["agent"]: b for b in ctx.db.heartbeats()}
        lines = [
            f"{ctx.db.get_setting('mode')} · paused {ctx.db.get_setting('paused')}",
            f"equity ${book['equity']:.2f} · pnl {book['pnl']:+.2f} · open {len(book['positions'])}",
        ]
        for agent in ("scanner", "auditor", "analyst", "executor", "risk", "writer"):
            beat = beats.get(agent)
            if beat:
                lines.append(f"{agent}: {beat['status']} {beat.get('detail') or ''}".rstrip())
        await _reply(ctx, chat_id, "\n".join(lines))
        return
    if name == "/stack":
        book = book_value(ctx.db)
        if not book["positions"]:
            await _reply(ctx, chat_id, "No open positions.")
            return
        lines = []
        for pos in book["positions"]:
            lines.append(
                f"{pos['symbol']} {pos['mode']} ${pos['size_usd']:.2f} "
                f"pnl {pos['pnl_usd']:+.2f} stop {pos['stop']:.6g}"
            )
        await _reply(ctx, chat_id, "\n".join(lines))
        return
    if name == "/invite":
        handle = ctx.db.get_setting("fomo_handle")
        signup = require_canonical(SIGNUP_URL)
        profile = require_canonical(profile_url(handle))
        await _reply(ctx, chat_id, f"Invite\n{signup}\nProfile\n{profile}")
        return
    if name == "/report":
        await _reply(ctx, chat_id, build_report(ctx.db))
        return
    if name == "/set":
        if len(args) < 2:
            await _reply(ctx, chat_id, "Usage: /set <key> <value>")
            return
        key, value = args[0], " ".join(args[1:])
        try:
            stored = validate_setting(key, value)
        except ValueError as exc:
            await _reply(ctx, chat_id, str(exc))
            return
        ctx.db.set_setting(key, stored, "set")
        note = ""
        if key in {"stop_pct", "trail_pct", "trail_arm_pct", "timeout_sec"}:
            note = " Open stops are not loosened."
        ctx.db.activity("set", f"{key} updated", None, {})
        await _reply(ctx, chat_id, f"{key} is {stored}.{note}")
        return
    if name == "/why":
        if not args:
            await _reply(ctx, chat_id, "Usage: /why <mint>")
            return
        mint = args[0]
        coin = ctx.db.coin(mint)
        layers = ctx.db.audits_for(mint)
        verdict = ctx.db.latest_verdict(mint)
        lines = [coin.get("symbol") or mint if coin else mint]
        if coin:
            lines.append(f"status {coin.get('status')} {coin.get('kill_reason') or ''}".rstrip())
        if not layers:
            lines.append("No audit yet.")
        for layer in layers:
            lines.append(f"{layer['layer']} {layer['name']} {layer['status']} {layer['detail']}")
        if verdict:
            lines.append(
                f"alive {verdict['alive']} organic {verdict['organic']} "
                f"flush {verdict['flush']} chase {verdict['chase']} "
                f"score {verdict['score']} {verdict['reason']}"
            )
        await _reply(ctx, chat_id, "\n".join(lines))
        return
    if name == "/buy":
        if not args:
            await _reply(ctx, chat_id, "Usage: /buy <mint>")
            return
        mint = args[0]
        last = ctx.db.latest_price(mint)
        if last is None or last.get("price_usd") is None:
            await _reply(ctx, chat_id, "No price for that mint yet.")
            return
        mode = ctx.db.get_setting("mode")
        score = 0
        verdict = ctx.db.latest_verdict(mint)
        if verdict and verdict.get("score") is not None:
            score = float(verdict["score"])
        signal_id = ctx.db.insert_signal(mint, float(last["price_usd"]), score, mode)
        coin = ctx.db.coin(mint) or {}
        await handle_signal(
            ctx,
            {
                "signal_id": signal_id,
                "mint": mint,
                "symbol": coin.get("symbol"),
                "name": coin.get("name"),
                "price": float(last["price_usd"]),
                "score": score,
                "metrics": {},
                "manual": True,
            },
        )
        sig = ctx.db.signal(signal_id)
        await _reply(ctx, chat_id, f"Buy handled ({sig['status'] if sig else 'unknown'}).")
        return
    if name == "/sell":
        if not args:
            await _reply(ctx, chat_id, "Usage: /sell <mint>")
            return
        mint = args[0]
        pos = ctx.db.position_for_mint(mint)
        if pos is None:
            await _reply(ctx, chat_id, "No open position.")
            return
        last = ctx.db.latest_price(mint)
        price = float(last["price_usd"]) if last and last.get("price_usd") is not None else float(pos["entry_price"])
        result = close_book(ctx, pos, price, "manual")
        if result is None:
            await _reply(ctx, chat_id, "Already closed.")
            return
        coin = ctx.db.coin(mint) or {}
        text = sell_text(pos["mode"], coin.get("symbol") or mint[:4], price, "manual", result["pnl"])
        text += "\nRecorded locally. No order was sent."
        await send(ctx, chat_id, text, sell_buttons(mint))
        return
    await _reply(ctx, chat_id, HELP)


async def on_callback(ctx, callback: dict) -> None:
    cb_id = callback.get("id")
    data = callback.get("data") or ""
    msg = callback.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if cb_id:
        await _call(ctx, "answerCallbackQuery", {"callback_query_id": cb_id})
    if not _allowed(ctx, chat_id):
        return
    if ":" not in data:
        return
    action, raw_id = data.split(":", 1)
    if not raw_id.isdigit():
        return
    signal_id = int(raw_id)
    if action == "bought":
        text = confirm_bought(ctx, signal_id)
    elif action == "skip":
        text = confirm_skip(ctx, signal_id)
    else:
        return
    await _reply(ctx, chat_id, text)


async def run(ctx, stop: asyncio.Event) -> None:
    if not ctx.cfg.tg_token:
        ctx.db.heartbeat("telegram", "disabled", "TG_TOKEN unset")
        await stop.wait()
        return
    queue = ctx.bus.subscribe("tg")
    await asyncio.sleep(0)
    offset = 0
    ctx.db.heartbeat("telegram", "ok", "polling")
    while not stop.is_set():
        push_task = asyncio.create_task(queue.get())
        poll_task = asyncio.create_task(
            _call(
                ctx,
                "getUpdates",
                {"timeout": 25, "offset": offset, "allowed_updates": ["message", "callback_query"]},
            )
        )
        done, _pending = await asyncio.wait(
            {push_task, poll_task},
            timeout=30,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop.is_set():
            push_task.cancel()
            poll_task.cancel()
            break
        if push_task in done:
            try:
                item = push_task.result()
            except Exception:
                item = None
            if item:
                owner = _owner(ctx)
                if owner:
                    await send(ctx, owner, item.get("text") or "", item.get("buttons") or None)
            if poll_task not in done:
                poll_task.cancel()
        if poll_task in done:
            if push_task not in done:
                push_task.cancel()
            try:
                data = poll_task.result()
            except Exception as exc:
                log.warning("telegram poll: %s", type(exc).__name__)
                data = None
            updates = (data or {}).get("result") if isinstance(data, dict) else None
            if data is None:
                await asyncio.sleep(2)
            for upd in updates or []:
                offset = int(upd.get("update_id", offset)) + 1
                try:
                    if upd.get("callback_query"):
                        await on_callback(ctx, upd["callback_query"])
                        continue
                    msg = upd.get("message") or {}
                    chat = msg.get("chat") or {}
                    if chat.get("type") != "private":
                        continue
                    parsed = parse_command(msg.get("text"))
                    if not parsed:
                        continue
                    name, args = parsed
                    log.info("telegram command %s", name)
                    await on_command(ctx, str(chat.get("id")), name, args)
                except Exception as exc:
                    log.warning("telegram update: %s", type(exc).__name__)
        else:
            if push_task not in done:
                push_task.cancel()
            if poll_task not in done:
                poll_task.cancel()
        ctx.db.heartbeat("telegram", "ok", "polling")
