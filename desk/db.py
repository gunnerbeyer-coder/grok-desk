"""SQLite book. WAL, one connection, short transactions.

Writers of `prices`:
- scanner, about every 60s, waiting-room coins
- risk, about every 15s, open positions
- api writer, about every 60s, other coins the app shows
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from desk.config import PUBLIC_SETTINGS, STEP_10_DEFAULTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS coins (
    mint TEXT PRIMARY KEY,
    name TEXT,
    symbol TEXT,
    pair_address TEXT,
    dex_id TEXT,
    pool_type TEXT,
    creator TEXT,
    pair_created_at INTEGER,
    first_seen INTEGER NOT NULL,
    status TEXT NOT NULL,
    kill_reason TEXT,
    decimals INTEGER,
    supply TEXT,
    token_program TEXT,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    ts INTEGER NOT NULL,
    liq_usd REAL,
    top10_pct REAL,
    price_change_h1 REAL,
    copycat INTEGER,
    age_s INTEGER,
    passed INTEGER,
    reason TEXT,
    price_usd REAL,
    raw_json TEXT
);
CREATE TABLE IF NOT EXISTS audits (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    ts INTEGER NOT NULL,
    layer INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    ts INTEGER NOT NULL,
    alive INTEGER,
    organic INTEGER,
    flush INTEGER,
    chase INTEGER,
    gate INTEGER,
    score REAL,
    reason TEXT,
    raw_json TEXT
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    ts INTEGER NOT NULL,
    price REAL,
    score REAL,
    size_usd REAL,
    status TEXT NOT NULL,
    mode TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    entry_price REAL NOT NULL,
    size_usd REAL NOT NULL,
    qty REAL NOT NULL,
    entry_fee REAL NOT NULL,
    stop REAL NOT NULL,
    trail_high REAL NOT NULL,
    opened_at INTEGER NOT NULL,
    deadline INTEGER NOT NULL,
    closed_at INTEGER,
    exit_price REAL,
    exit_fee REAL,
    exit_reason TEXT,
    pnl REAL,
    signal_id INTEGER
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    kind TEXT NOT NULL,
    mint TEXT,
    message TEXT,
    meta_json TEXT
);
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    ts INTEGER NOT NULL,
    price_usd REAL,
    liq_usd REAL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prices_mint_ts ON prices(mint, ts);
CREATE TABLE IF NOT EXISTS balance (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    equity REAL,
    cash REAL,
    mode TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    key TEXT,
    old TEXT,
    new TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS skips (
    id INTEGER PRIMARY KEY,
    mint TEXT,
    ts INTEGER NOT NULL,
    reason TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS heartbeats (
    agent TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    status TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS push_subs (
    id INTEGER PRIMARY KEY,
    endpoint TEXT UNIQUE,
    p256dh TEXT,
    auth TEXT,
    created_at INTEGER
);
"""


def _now() -> int:
    return int(time.time())


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level="DEFERRED")
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._tx = 0

    def init(self) -> None:
        with self.lock:
            self.conn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;")
            self.conn.executescript(SCHEMA)
            now = _now()
            for key, value in STEP_10_DEFAULTS.items():
                self.conn.execute(
                    "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO NOTHING",
                    (key, value, now),
                )
            self.conn.commit()
            if self.get_setting("cash") == "":
                bank = self.get_setting("bankroll_usd")
                self._set_raw("cash", bank, "init")
                self._set_raw("starting_equity", bank, "init")
            elif self.get_setting("starting_equity") == "":
                self._set_raw("starting_equity", self.get_setting("cash"), "init")

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            self._tx += 1
            try:
                yield
                if self._tx == 1:
                    self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            finally:
                self._tx -= 1

    def _commit(self) -> None:
        if self._tx == 0:
            self.conn.commit()

    def get_setting(self, key: str) -> str:
        with self.lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if row is not None:
                return str(row["value"])
            if key in STEP_10_DEFAULTS:
                return STEP_10_DEFAULTS[key]
            raise KeyError(key)

    def settings_map(self) -> dict[str, str]:
        with self.lock:
            rows = self.conn.execute("SELECT key, value FROM settings").fetchall()
            found = {r["key"]: str(r["value"]) for r in rows}
            for key, value in STEP_10_DEFAULTS.items():
                found.setdefault(key, value)
            return found

    def public_settings(self) -> dict[str, str]:
        data = self.settings_map()
        return {key: data.get(key, "") for key in PUBLIC_SETTINGS}

    def _set_raw(self, key: str, value: str, source: str) -> None:
        old = ""
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is not None:
            old = str(row["value"])
        if old == value:
            return
        now = _now()
        self.conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, now),
        )
        self.conn.execute(
            "INSERT INTO change_log(ts, key, old, new, source) VALUES(?,?,?,?,?)",
            (now, key, old, value, source),
        )
        self._commit()

    def set_setting(self, key: str, value: str, source: str) -> None:
        with self.lock:
            self._set_raw(key, str(value), source)

    def claim_owner(self, chat_id: str) -> bool:
        """First /start wins. Returns True when this chat is the owner."""
        with self.lock:
            current = self.get_setting("owner_chat_id")
            if current and current != str(chat_id):
                return False
            if not current:
                self._set_raw("owner_chat_id", str(chat_id), "start")
            return True

    def heartbeat(self, agent: str, status: str, detail: str) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO heartbeats(agent, ts, status, detail) VALUES(?,?,?,?) "
                "ON CONFLICT(agent) DO UPDATE SET ts=excluded.ts, status=excluded.status, detail=excluded.detail",
                (agent, _now(), status, detail[:240]),
            )
            self._commit()

    def heartbeats(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM heartbeats").fetchall()
            return [dict(r) for r in rows]

    def activity(self, kind: str, message: str, mint: str | None = None, meta: dict | None = None) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO activity(ts, kind, mint, message, meta_json) VALUES(?,?,?,?,?)",
                (_now(), kind, mint, message[:500], json.dumps(meta or {})),
            )
            self._commit()

    def recent_activity(self, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, ts, kind, mint, message FROM activity ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def skip(self, mint: str | None, reason: str, source: str) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO skips(mint, ts, reason, source) VALUES(?,?,?,?)",
                (mint, _now(), reason, source),
            )
            self._commit()

    def upsert_coin(self, mint: str, **fields) -> None:
        with self.lock:
            now = _now()
            row = self.conn.execute("SELECT mint, first_seen FROM coins WHERE mint=?", (mint,)).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO coins(mint, name, symbol, pair_address, dex_id, pool_type, creator, "
                    "pair_created_at, first_seen, status, kill_reason, decimals, supply, token_program, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        mint,
                        fields.get("name"),
                        fields.get("symbol"),
                        fields.get("pair_address"),
                        fields.get("dex_id"),
                        fields.get("pool_type"),
                        fields.get("creator"),
                        fields.get("pair_created_at"),
                        fields.get("first_seen", now),
                        fields.get("status", "waiting"),
                        fields.get("kill_reason"),
                        fields.get("decimals"),
                        fields.get("supply"),
                        fields.get("token_program"),
                        now,
                    ),
                )
            else:
                sets = []
                vals = []
                for key in (
                    "name",
                    "symbol",
                    "pair_address",
                    "dex_id",
                    "pool_type",
                    "creator",
                    "pair_created_at",
                    "status",
                    "kill_reason",
                    "decimals",
                    "supply",
                    "token_program",
                ):
                    if key in fields and fields[key] is not None:
                        sets.append(f"{key}=?")
                        vals.append(fields[key])
                sets.append("updated_at=?")
                vals.append(now)
                vals.append(mint)
                self.conn.execute(f"UPDATE coins SET {', '.join(sets)} WHERE mint=?", vals)
            self._commit()

    def coin(self, mint: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM coins WHERE mint=?", (mint,)).fetchone()
            return dict(row) if row else None

    def coins_by_status(self, statuses: tuple[str, ...]) -> list[dict]:
        if not statuses:
            return []
        marks = ",".join("?" for _ in statuses)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM coins WHERE status IN ({marks}) ORDER BY updated_at DESC",
                statuses,
            ).fetchall()
            return [dict(r) for r in rows]

    def recent_coins(self, limit: int = 40) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM coins ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def symbols_taken(self, exclude_mint: str | None = None) -> set[str]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT symbol, mint FROM coins WHERE symbol IS NOT NULL AND status != 'killed'"
            ).fetchall()
            out = set()
            for row in rows:
                if exclude_mint and row["mint"] == exclude_mint:
                    continue
                if row["symbol"]:
                    out.add(str(row["symbol"]).upper())
            return out

    def insert_scan(self, **fields) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO scans(mint, ts, liq_usd, top10_pct, price_change_h1, copycat, age_s, "
                "passed, reason, price_usd, raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fields["mint"],
                    fields.get("ts", _now()),
                    fields.get("liq_usd"),
                    fields.get("top10_pct"),
                    fields.get("price_change_h1"),
                    1 if fields.get("copycat") else 0,
                    fields.get("age_s"),
                    1 if fields.get("passed") else 0,
                    fields.get("reason"),
                    fields.get("price_usd"),
                    json.dumps(fields.get("raw") or {}),
                ),
            )
            self._commit()

    def replace_audits(self, mint: str, layers: list[dict]) -> None:
        with self.lock:
            now = _now()
            self.conn.execute("DELETE FROM audits WHERE mint=?", (mint,))
            for layer in layers:
                self.conn.execute(
                    "INSERT INTO audits(mint, ts, layer, name, status, detail) VALUES(?,?,?,?,?,?)",
                    (mint, now, layer["layer"], layer["name"], layer["status"], layer.get("detail", "")),
                )
            self._commit()

    def latest_audit_mint(self) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT mint FROM audits ORDER BY id DESC LIMIT 1").fetchone()
            return row["mint"] if row else None

    def audits_for(self, mint: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT layer, name, status, detail, ts FROM audits WHERE mint=? ORDER BY layer",
                (mint,),
            ).fetchall()
            return [dict(r) for r in rows]

    def insert_verdict(self, **fields) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO verdicts(mint, ts, alive, organic, flush, chase, gate, score, reason, raw_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    fields["mint"],
                    fields.get("ts", _now()),
                    1 if fields.get("alive") else 0,
                    1 if fields.get("organic") else 0,
                    1 if fields.get("flush") else 0,
                    1 if fields.get("chase") else 0,
                    1 if fields.get("gate") else 0,
                    fields.get("score"),
                    fields.get("reason"),
                    json.dumps(fields.get("raw") or {}),
                ),
            )
            self._commit()

    def latest_verdict(self, mint: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM verdicts WHERE mint=? ORDER BY id DESC LIMIT 1",
                (mint,),
            ).fetchone()
            return dict(row) if row else None

    def insert_signal(self, mint: str, price: float, score: float, mode: str) -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO signals(mint, ts, price, score, size_usd, status, mode) VALUES(?,?,?,?,?,?,?)",
                (mint, _now(), price, score, None, "new", mode),
            )
            self._commit()
            return int(cur.lastrowid)

    def signal(self, signal_id: int) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
            return dict(row) if row else None

    def insert_price(self, mint: str, price: float | None, liq: float | None, source: str) -> None:
        if price is None and liq is None:
            return
        with self.lock:
            self.conn.execute(
                "INSERT INTO prices(mint, ts, price_usd, liq_usd, source) VALUES(?,?,?,?,?)",
                (mint, _now(), price, liq, source),
            )
            self._commit()

    def latest_price(self, mint: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT ts, price_usd, liq_usd, source FROM prices WHERE mint=? ORDER BY ts DESC, id DESC LIMIT 1",
                (mint,),
            ).fetchone()
            return dict(row) if row else None

    def price_series(self, mint: str, limit: int = 240) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT ts, price_usd FROM prices WHERE mint=? AND price_usd IS NOT NULL "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (mint, limit),
            ).fetchall()
            return [{"t": r["ts"], "p": r["price_usd"]} for r in reversed(rows)]

    def prune_prices(self, before_ts: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM prices WHERE ts < ?", (before_ts,))
            self._commit()

    def open_positions(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM positions WHERE status='open' ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def position_for_mint(self, mint: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM positions WHERE mint=? AND status='open' ORDER BY id DESC LIMIT 1",
                (mint,),
            ).fetchone()
            return dict(row) if row else None

    def open_count(self) -> int:
        with self.lock:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM positions WHERE status='open'").fetchone()
            return int(row["n"])

    def record_balance(self, equity: float, cash: float, mode: str) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO balance(ts, equity, cash, mode) VALUES(?,?,?,?)",
                (_now(), equity, cash, mode),
            )
            self._commit()

    def closed_stats(self) -> dict:
        with self.lock:
            rows = self.conn.execute(
                "SELECT pnl FROM positions WHERE status='closed' AND pnl IS NOT NULL"
            ).fetchall()
            pnls = [float(r["pnl"]) for r in rows]
            wins = sum(1 for p in pnls if p > 0)
            return {
                "closed": len(pnls),
                "wins": wins,
                "losses": sum(1 for p in pnls if p <= 0),
                "realized": sum(pnls),
            }

    def fill_new_signal(self, signal_id: int, plan: dict, mode: str, deadline: int) -> int | None:
        """Paper or confirmed fill. Returns the position id, or None if the signal was already taken."""
        with self.lock:
            with self.transaction():
                cur = self.conn.execute(
                    "UPDATE signals SET status='filled', size_usd=? WHERE id=? AND status IN ('new','awaiting')",
                    (plan["size_usd"], signal_id),
                )
                if cur.rowcount != 1:
                    return None
                sig = self.conn.execute("SELECT mint FROM signals WHERE id=?", (signal_id,)).fetchone()
                cash = float(self.get_setting("cash"))
                debit = float(plan["size_usd"]) + float(plan["entry_fee"])
                if debit > cash + 1e-9:
                    raise RuntimeError("cash shortfall")
                self._set_raw("cash", f"{cash - debit:.8f}", "fill")
                now = _now()
                cur = self.conn.execute(
                    "INSERT INTO positions(mint, mode, status, entry_price, size_usd, qty, entry_fee, stop, "
                    "trail_high, opened_at, deadline, signal_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sig["mint"],
                        mode,
                        "open",
                        plan["fill_price"],
                        plan["size_usd"],
                        plan["qty"],
                        plan["entry_fee"],
                        plan["stop"],
                        plan["fill_price"],
                        now,
                        deadline,
                        signal_id,
                    ),
                )
                self.conn.execute(
                    "UPDATE coins SET status='open', updated_at=? WHERE mint=?",
                    (now, sig["mint"]),
                )
                return int(cur.lastrowid)

    def mark_awaiting(self, signal_id: int, size_usd: float) -> bool:
        with self.lock:
            cur = self.conn.execute(
                "UPDATE signals SET status='awaiting', size_usd=? WHERE id=? AND status='new'",
                (size_usd, signal_id),
            )
            self._commit()
            return cur.rowcount == 1

    def mark_skipped(self, signal_id: int) -> bool:
        with self.lock:
            cur = self.conn.execute(
                "UPDATE signals SET status='skipped' WHERE id=? AND status IN ('new','awaiting')",
                (signal_id,),
            )
            self._commit()
            return cur.rowcount == 1

    def update_stop(self, position_id: int, stop: float, high: float) -> None:
        """Stop may only rise. High water may only rise."""
        with self.lock:
            self.conn.execute(
                "UPDATE positions SET stop=CASE WHEN ? >= stop THEN ? ELSE stop END, "
                "trail_high=MAX(trail_high, ?) WHERE id=? AND status='open'",
                (stop, stop, high, position_id),
            )
            self._commit()

    def close_position(self, position_id: int, exit_price: float, exit_fee: float, reason: str, pnl: float) -> bool:
        with self.lock:
            with self.transaction():
                row = self.conn.execute(
                    "SELECT * FROM positions WHERE id=? AND status='open'",
                    (position_id,),
                ).fetchone()
                if row is None:
                    return False
                proceeds = float(row["qty"]) * float(exit_price)
                cash = float(self.get_setting("cash"))
                self._set_raw("cash", f"{cash + proceeds - float(exit_fee):.8f}", "exit")
                now = _now()
                self.conn.execute(
                    "UPDATE positions SET status='closed', closed_at=?, exit_price=?, exit_fee=?, "
                    "exit_reason=?, pnl=? WHERE id=?",
                    (now, exit_price, exit_fee, reason, pnl, position_id),
                )
                self.conn.execute(
                    "UPDATE coins SET status='closed', updated_at=? WHERE mint=?",
                    (now, row["mint"]),
                )
                return True
