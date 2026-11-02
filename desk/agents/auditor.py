"""Eight-layer audit. The first red layer stops the run.

Layer 8 is getProgramAccounts on Token and Token-2022. RugCheck insider
networks are read from `links`, never `edges`.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import struct

from desk.fields import (
    GRAPH_HOLDINGS,
    GRAPH_LINKS,
    GRAPH_NODE_ID,
    GRAPH_NODES,
    GRAPH_SOURCE,
    GRAPH_TARGET,
    HOLDER_ADDRESS,
    HOLDER_INSIDER,
    HOLDER_OWNER,
    HOLDER_PCT,
    MARKET_PUBKEY,
    MARKET_TYPE,
    RPC_MINT,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    b58encode,
    dig,
)
from desk.vendors import gpa_params, programs_for_holders

log = logging.getLogger("desk.auditor")

LAYER_NAMES = (
    "mint_authority",
    "freeze_authority",
    "token_program",
    "market_match",
    "lp_lock",
    "top_holders",
    "insider_graph",
    "program_accounts",
)

PUMP_MARKET_TYPES = {"pump_fun_amm", "pumpswap", "pump_amm"}


def parse_gpa_slice(data_b64: str) -> tuple[str, int]:
    raw = base64.b64decode(data_b64)
    if len(raw) < 40:
        raise ValueError("short token account slice")
    owner = b58encode(raw[:32])
    amount = struct.unpack_from("<Q", raw, 32)[0]
    return owner, amount


def read_mint_account(resp: dict) -> dict:
    info_path = "result.value.data.parsed.info"
    info = dig(resp, info_path)
    info = info if isinstance(info, dict) else {}
    program = dig(resp, RPC_MINT["tokenProgram"])
    exists = dig(resp, "result.value") is not None
    return {
        "exists": exists,
        "mint_authority_known": "mintAuthority" in info,
        "mint_authority": info.get("mintAuthority") if "mintAuthority" in info else None,
        "freeze_known": "freezeAuthority" in info,
        "freeze_authority": info.get("freezeAuthority") if "freezeAuthority" in info else None,
        "supply": info.get("supply") if "supply" in info else None,
        "decimals": info.get("decimals") if "decimals" in info else None,
        "token_program": program,
    }


def normalize_markets(report: dict | None) -> list[dict]:
    if not isinstance(report, dict):
        return []
    markets = report.get("markets")
    if not isinstance(markets, list):
        return []
    out = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        lp = market.get("lp") if isinstance(market.get("lp"), dict) else {}
        out.append(
            {
                "pubkey": market.get(MARKET_PUBKEY),
                "marketType": market.get(MARKET_TYPE),
                "lpLockedPct": lp.get("lpLockedPct"),
            }
        )
    return out


def normalize_holders(report: dict | None) -> list[dict]:
    if not isinstance(report, dict):
        return []
    holders = report.get("topHolders")
    if not isinstance(holders, list):
        return []
    out = []
    for holder in holders:
        if not isinstance(holder, dict):
            continue
        out.append(
            {
                "address": holder.get(HOLDER_ADDRESS),
                "owner": holder.get(HOLDER_OWNER),
                "pct": holder.get(HOLDER_PCT),
                "insider": holder.get(HOLDER_INSIDER),
            }
        )
    return out


def cluster_size(networks: list) -> int:
    """Largest set of holding wallets joined by `links` in one network."""
    worst = 0
    for net in networks or []:
        if not isinstance(net, dict):
            continue
        nodes = net.get(GRAPH_NODES) if isinstance(net.get(GRAPH_NODES), list) else []
        links = net.get(GRAPH_LINKS) if isinstance(net.get(GRAPH_LINKS), list) else []
        joined: set[str] = set()
        for link in links:
            if not isinstance(link, dict):
                continue
            src = link.get(GRAPH_SOURCE)
            dst = link.get(GRAPH_TARGET)
            if src:
                joined.add(src)
            if dst:
                joined.add(dst)
        holding = 0
        for node in nodes:
            if not isinstance(node, dict):
                continue
            wallet = node.get(GRAPH_NODE_ID)
            try:
                held = float(node.get(GRAPH_HOLDINGS) or 0)
            except (TypeError, ValueError):
                held = 0
            if wallet in joined and held > 0:
                holding += 1
        worst = max(worst, holding)
    return worst


def _pct_list(holders: list[dict], pool_ids: set[str]) -> list[float]:
    pcts = []
    for holder in holders:
        if holder.get("owner") in pool_ids or holder.get("address") in pool_ids:
            continue
        raw = holder.get("pct")
        if isinstance(raw, (int, float)):
            pcts.append(float(raw))
    if pcts and max(pcts) <= 1:
        pcts = [p * 100.0 for p in pcts]
    pcts.sort(reverse=True)
    return pcts


def concentration(amounts: list[int], supply: int) -> float | None:
    if supply <= 0:
        return None
    positive = sorted((a for a in amounts if a > 0), reverse=True)
    if not positive:
        return None
    return sum(positive[:10]) / float(supply) * 100.0


def _layer(n: int, status: str, detail: str) -> dict:
    return {"layer": n, "name": LAYER_NAMES[n - 1], "status": status, "detail": detail}


def audit_layers(info: dict, settings: dict) -> list[dict]:
    """Run layers 1–8. Stop at the first red. `info` is already normalized."""
    layers: list[dict] = []

    def stop(n: int, detail: str) -> list[dict]:
        layers.append(_layer(n, "red", detail))
        return layers

    def ok(n: int, detail: str) -> None:
        layers.append(_layer(n, "pass", detail))

    if not info.get("mint_authority_known"):
        return stop(1, "mint authority missing")
    if info.get("mint_authority") is not None:
        return stop(1, "mint authority still set")
    ok(1, "revoked")

    if not info.get("freeze_known"):
        return stop(2, "freeze authority missing")
    if info.get("freeze_authority") is not None:
        return stop(2, "freeze authority still set")
    ok(2, "revoked")

    program = info.get("token_program")
    if program not in {TOKEN_PROGRAM, TOKEN_2022_PROGRAM}:
        return stop(3, "token program not allowlisted")
    label = "token-2022" if program == TOKEN_2022_PROGRAM else "token"
    ok(3, label)

    pair = info.get("pair_address")
    markets = info.get("markets") or []
    match = None
    for market in markets:
        if market.get("pubkey") and market.get("pubkey") == pair:
            match = market
            break
    if match is None:
        return stop(4, "no market for pair address")
    mtype = str(match.get("marketType") or "")
    if mtype not in PUMP_MARKET_TYPES:
        return stop(4, f"market type {mtype or 'unknown'}")
    ok(4, mtype)

    locked = match.get("lpLockedPct")
    try:
        locked_f = float(locked)
    except (TypeError, ValueError):
        return stop(5, "lp lock missing")
    floor = float(settings["min_lp_locked_pct"])
    if locked_f < floor:
        return stop(5, f"lp locked {locked_f:.1f}%")
    ok(5, f"lp locked {locked_f:.1f}%")

    pool_ids = {m.get("pubkey") for m in markets if m.get("pubkey")}
    if pair:
        pool_ids.add(pair)
    pcts = _pct_list(info.get("holders") or [], pool_ids)
    if not pcts:
        return stop(6, "no holders")
    top = sum(pcts[:10])
    cap = float(settings["max_top10_pct"])
    if top > cap:
        return stop(6, f"top10 {top:.1f}%")
    raw_pcts = [
        float(h["pct"])
        for h in (info.get("holders") or [])
        if isinstance(h.get("pct"), (int, float))
    ]
    scale = 100.0 if raw_pcts and max(raw_pcts) <= 1 else 1.0
    insider_hit = False
    for holder in info.get("holders") or []:
        if holder.get("owner") in pool_ids or holder.get("address") in pool_ids:
            continue
        if holder.get("insider") and isinstance(holder.get("pct"), (int, float)) and float(holder["pct"]) * scale > 10:
            insider_hit = True
    if insider_hit:
        return stop(6, "insider holder over 10%")
    ok(6, f"top10 {top:.1f}%")

    if not info.get("graph_ok"):
        return stop(7, "graph unavailable")
    size = cluster_size(info.get("graph") or [])
    limit = int(float(settings["insider_cluster_min"]))
    if size >= limit:
        return stop(7, f"insider cluster {size}")
    ok(7, f"cluster {size}")

    if not info.get("gpa_ok"):
        return stop(8, "program accounts unavailable")
    supply_raw = info.get("supply")
    try:
        supply = int(supply_raw)
    except (TypeError, ValueError):
        return stop(8, "supply missing")
    amounts = info.get("amounts") or []
    if not amounts:
        return stop(8, "no token accounts")
    conc = concentration([int(a) for a in amounts], supply)
    if conc is None:
        return stop(8, "empty accounts")
    if conc > cap:
        return stop(8, f"on-chain top10 {conc:.1f}%")
    ok(8, f"on-chain top10 {conc:.1f}%")
    return layers


def _account_data(entry: dict) -> str | None:
    account = entry.get("account") if isinstance(entry, dict) else None
    if not isinstance(account, dict):
        return None
    data = account.get("data")
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, str):
        return data
    return None


async def _holder_amounts(ctx, mint: str, pool_ids: set[str]) -> tuple[bool, list[int]]:
    amounts: list[int] = []
    ok = True
    for program in programs_for_holders():
        try:
            body = await ctx.vendors.rpc("getProgramAccounts", gpa_params(program, mint))
        except Exception as exc:
            log.warning("gpa failed: %s", type(exc).__name__)
            ok = False
            continue
        result = body.get("result")
        if not isinstance(result, list):
            ok = False
            continue
        for entry in result:
            encoded = _account_data(entry)
            if not encoded:
                continue
            try:
                owner, amount = parse_gpa_slice(encoded)
            except Exception:
                continue
            if owner in pool_ids or amount <= 0:
                continue
            amounts.append(amount)
    return ok, amounts


async def audit_one(ctx, payload: dict) -> None:
    mint = payload["mint"]
    settings = ctx.db.settings_map()
    info = {
        "pair_address": payload.get("pair_address"),
        "mint_authority_known": False,
        "freeze_known": False,
        "markets": [],
        "holders": [],
        "graph_ok": False,
        "graph": [],
        "gpa_ok": False,
        "amounts": [],
        "supply": None,
        "token_program": None,
    }
    try:
        rpc_body = await ctx.vendors.rpc(
            "getAccountInfo",
            [mint, {"encoding": "jsonParsed"}],
        )
        mint_info = read_mint_account(rpc_body)
        info.update(mint_info)
    except Exception as exc:
        log.warning("mint account failed: %s", type(exc).__name__)

    report = await ctx.vendors.rug_report(mint)
    info["markets"] = normalize_markets(report)
    info["holders"] = normalize_holders(report)
    if isinstance(report, dict):
        creator = report.get("creator")
        if creator:
            ctx.db.upsert_coin(mint, creator=creator)

    graph_ok, graph = await ctx.vendors.rug_graph(mint)
    info["graph_ok"] = graph_ok
    info["graph"] = graph

    pool_ids = {m.get("pubkey") for m in info["markets"] if m.get("pubkey")}
    if payload.get("pair_address"):
        pool_ids.add(payload["pair_address"])
    gpa_ok, amounts = await _holder_amounts(ctx, mint, pool_ids)
    info["gpa_ok"] = gpa_ok
    info["amounts"] = amounts
    if info.get("supply") is None and isinstance(report, dict):
        token = report.get("token") if isinstance(report.get("token"), dict) else {}
        if token.get("supply") is not None:
            info["supply"] = token.get("supply")

    layers = audit_layers(info, settings)
    ctx.db.replace_audits(mint, layers)
    for layer in layers:
        ctx.bus.publish(
            "audit_layer",
            {"mint": mint, "symbol": payload.get("symbol"), **layer},
        )
    passed = bool(layers) and layers[-1]["status"] == "pass" and len(layers) == 8
    symbol = payload.get("symbol") or mint[:4]
    if passed:
        ctx.db.upsert_coin(
            mint,
            status="audit_pass",
            token_program=info.get("token_program"),
            decimals=info.get("decimals") if isinstance(info.get("decimals"), int) else None,
            supply=str(info["supply"]) if info.get("supply") is not None else None,
        )
        ctx.db.activity("audit", f"{symbol} 8/8", mint, {})
        ctx.db.heartbeat("auditor", "ok", f"8/8 {symbol}")
        ctx.bus.publish(
            "audit_pass",
            {
                "mint": mint,
                "symbol": payload.get("symbol"),
                "name": payload.get("name"),
                "pair_address": payload.get("pair_address"),
                "metrics": payload.get("metrics") or {},
            },
        )
    else:
        failed = layers[-1] if layers else {"name": "audit", "detail": "no layers"}
        ctx.db.upsert_coin(mint, status="rejected", kill_reason=failed["name"])
        ctx.db.activity("audit", f"{symbol} red {failed['name']}", mint, {"detail": failed["detail"]})
        ctx.db.heartbeat("auditor", "ok", f"red {failed['name']} {symbol}")


async def run(ctx, stop: asyncio.Event) -> None:
    queue = ctx.bus.subscribe("scan_pass")
    await asyncio.sleep(0)
    while not stop.is_set():
        try:
            item = await asyncio.wait_for(queue.get(), timeout=5)
        except asyncio.TimeoutError:
            ctx.db.heartbeat("auditor", "ok", "idle")
            continue
        try:
            coin = ctx.db.coin(item["mint"])
            if coin and coin.get("status") in {"audit_pass", "rejected", "open", "closed", "signal"}:
                continue
            await audit_one(ctx, item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("audit failed: %s", type(exc).__name__)
            ctx.db.heartbeat("auditor", "error", type(exc).__name__)
