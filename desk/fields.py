"""Key paths verified against logs/raw/* on 2026-11-01. None = not present in raw sample.

Read external JSON only through these paths. Do not invent keys.
"""

from __future__ import annotations

PUMPPORTAL_MIGRATION = {  # logs/raw/pumpportal_migration.json
    "mint": "mint",
    "signature": "signature",
    "txType": "txType",
    "pool": "pool",  # a pool TYPE string ("raydium-cpmm"), not a pool address
    "creator": None,  # not in message
    "name": None,
    "symbol": None,
}
DEXSCREENER = {  # logs/raw/dexscreener_tokens.json (list of pairs)
    "pairAddress": "pairAddress",
    "dexId": "dexId",
    "baseMint": "baseToken.address",
    "name": "baseToken.name",
    "symbol": "baseToken.symbol",
    "priceUsd": "priceUsd",  # string
    "liquidity_usd": "liquidity.usd",
    "marketCap": "marketCap",
    "volume_m5": "volume.m5",
    "volume_h1": "volume.h1",
    "volume_h24": "volume.h24",
    "buys_m5": "txns.m5.buys",
    "sells_m5": "txns.m5.sells",
    "buys_h1": "txns.h1.buys",
    "sells_h1": "txns.h1.sells",
    "priceChange_m5": "priceChange.m5",
    "priceChange_h1": "priceChange.h1",
    "priceChange_h24": "priceChange.h24",
    "pairCreatedAt": "pairCreatedAt",  # ms epoch
}
RPC_MINT = {  # logs/raw/rpc_getAccountInfo.json
    "mintAuthority": "result.value.data.parsed.info.mintAuthority",
    "freezeAuthority": "result.value.data.parsed.info.freezeAuthority",
    "supply": "result.value.data.parsed.info.supply",  # string, raw units
    "decimals": "result.value.data.parsed.info.decimals",
    "tokenProgram": "result.value.owner",
}
RUGCHECK_REPORT = {  # logs/raw/rugcheck_report.json
    "creator": "creator",
    "creatorBalance": "creatorBalance",
    "mintAuthority": "mintAuthority",
    "freezeAuthority": "freezeAuthority",
    "supply": "token.supply",
    "decimals": "token.decimals",
    "markets": "markets",
    "market_pool": "markets[].pubkey",
    "market_type": "markets[].marketType",  # "pump_fun_amm"
    "lp_locked_pct": "markets[].lp.lpLockedPct",
    "lp_burned_pct": None,  # no separate burned key; lpLockedPct covers locked+burned
    "top_holders": "topHolders",
    "top_holder_address": "topHolders[].address",
    "top_holder_owner": "topHolders[].owner",
    "top_holder_pct": "topHolders[].pct",
    "top_holder_insider": "topHolders[].insider",
    "totalHolders": "totalHolders",
}
RUGCHECK_GRAPH = {  # logs/raw/rugcheck_insiders_graph.json (list of networks)
    "networks": "[]",
    "network_type": "[].network_type",
    "nodes": "[].nodes",
    "node_wallet": "[].nodes[].id",
    "holder_flag": "[].nodes[].holdings",  # >0 = holds; also [].nodes[].participant (bool)
    "edges": "[].links",
    "edge_from": "[].links[].source",
    "edge_to": "[].links[].target",
}
RPC_LARGEST = {"top_holders": None}  # getTokenLargestAccounts: 429 on public RPC, unverified
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Leaf names taken from the verified paths above.
HOLDER_ADDRESS = "address"
HOLDER_OWNER = "owner"
HOLDER_PCT = "pct"
HOLDER_INSIDER = "insider"
MARKET_PUBKEY = "pubkey"
MARKET_TYPE = "marketType"
GRAPH_NETWORK_TYPE = "network_type"
GRAPH_NODES = "nodes"
GRAPH_LINKS = "links"
GRAPH_NODE_ID = "id"
GRAPH_HOLDINGS = "holdings"
GRAPH_PARTICIPANT = "participant"
GRAPH_SOURCE = "source"
GRAPH_TARGET = "target"


def dig(obj, path: str | None):
    """Walk a dotted path. Missing keys return None. Does not walk [] wildcards."""
    if path is None or obj is None:
        return None
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def map_fields(obj: dict, mapping: dict) -> dict:
    out = {}
    for name, path in mapping.items():
        out[name] = None if path is None else dig(obj, path)
    return out


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw: bytes) -> str:
    if not raw:
        return ""
    n = int.from_bytes(raw, "big")
    chars = []
    while n:
        n, rem = divmod(n, 58)
        chars.append(_B58[rem])
    pad = 0
    for byte in raw:
        if byte == 0:
            pad += 1
        else:
            break
    encoded = "".join(reversed(chars)) if chars else ""
    return ("1" * pad) + encoded
