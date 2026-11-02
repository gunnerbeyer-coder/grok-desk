"""Read-only clients: PumpPortal is opened by the scanner. DexScreener, RugCheck, RPC."""

from __future__ import annotations

import logging

import httpx

from desk.fields import TOKEN_2022_PROGRAM, TOKEN_PROGRAM

log = logging.getLogger("desk.vendors")

DEX_URL = "https://api.dexscreener.com/tokens/v1/solana/"
RUG_REPORT = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
RUG_GRAPH = "https://api.rugcheck.xyz/v1/tokens/{mint}/insiders/graph"
PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"


def gpa_params(program: str, mint: str) -> list:
    """getProgramAccounts params. Token-2022 accounts can be larger than 165."""
    filters: list[dict] = [{"memcmp": {"offset": 0, "bytes": mint}}]
    if program == TOKEN_PROGRAM:
        filters.insert(0, {"dataSize": 165})
    return [
        program,
        {
            "encoding": "base64",
            "dataSlice": {"offset": 32, "length": 40},
            "filters": filters,
        },
    ]


def programs_for_holders() -> tuple[str, str]:
    return (TOKEN_PROGRAM, TOKEN_2022_PROGRAM)


class Vendors:
    def __init__(self, http: httpx.AsyncClient, rpc_url: str):
        self.http = http
        self.rpc_url = rpc_url

    async def dexscreener(self, mints: list[str]) -> list[dict]:
        clean = [m for m in mints if m and "," not in m and "/" not in m]
        if not clean:
            return []
        out: list[dict] = []
        for i in range(0, len(clean), 30):
            chunk = ",".join(clean[i : i + 30])
            try:
                resp = await self.http.get(DEX_URL + chunk, timeout=12)
                resp.raise_for_status()
                body = resp.json()
            except Exception as exc:
                log.warning("dexscreener failed: %s", type(exc).__name__)
                continue
            if isinstance(body, list):
                out.extend(item for item in body if isinstance(item, dict))
            else:
                log.warning("dexscreener returned %s", type(body).__name__)
        return out

    async def rug_report(self, mint: str) -> dict | None:
        try:
            resp = await self.http.get(RUG_REPORT.format(mint=mint), timeout=15)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            body = resp.json()
            return body if isinstance(body, dict) else None
        except Exception as exc:
            log.warning("rugcheck report failed: %s", type(exc).__name__)
            return None

    async def rug_graph(self, mint: str) -> tuple[bool, list]:
        """(ok, networks). A 404 or empty list is a real empty graph."""
        try:
            resp = await self.http.get(RUG_GRAPH.format(mint=mint), timeout=15)
            if resp.status_code == 404:
                return True, []
            resp.raise_for_status()
            body = resp.json()
            if isinstance(body, list):
                return True, body
            return False, []
        except Exception as exc:
            log.warning("rugcheck graph failed: %s", type(exc).__name__)
            return False, []

    async def rpc(self, method: str, params: list) -> dict:
        if method not in {"getAccountInfo", "getProgramAccounts"}:
            raise RuntimeError("rpc method is not a read")
        if not self.rpc_url:
            raise RuntimeError("rpc unconfigured")
        resp = await self.http.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=20,
        )
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict):
            raise RuntimeError("rpc body")
        if body.get("error"):
            raise RuntimeError("rpc error")
        return body
