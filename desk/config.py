"""Environment and Step 10 defaults.

Secrets stay in the environment. Nothing here writes them to disk.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]

# Step 10. Stored in SQLite settings. Strings, so /set writes the same shape.
# analyst_mode stays "rules". notifier stays "telegram".
STEP_10_DEFAULTS: dict[str, str] = {
    "mode": "paper",
    "paused": "0",
    "analyst_mode": "rules",
    "notifier": "telegram",
    "fomo_handle": "chillem122",
    "fees_ref_discount": "0.10",
    "min_liq_usd": "40000",
    "max_top10_pct": "25",
    "max_h1_pct": "80",
    "min_age_sec": "180",
    "waiting_room_max_sec": "900",
    "min_lp_locked_pct": "90",
    "insider_cluster_min": "5",
    "alive_min_txns_m5": "8",
    "alive_min_volume_m5": "1000",
    "organic_min_buy_ratio": "0.35",
    "organic_max_buy_ratio": "0.80",
    "flush_m5_pct": "-12",
    "chase_h1_pct": "40",
    "min_score": "60",
    "bankroll_usd": "1000",
    "risk_per_trade_pct": "2",
    "max_position_usd": "100",
    "min_position_usd": "15",
    "max_open_positions": "3",
    "stop_pct": "12",
    "trail_pct": "15",
    "trail_arm_pct": "10",
    "timeout_sec": "3600",
    "owner_chat_id": "",
    "cash": "",
    "starting_equity": "",
}

# Shown on the Account tab. Cash and the owner id stay out of this list.
PUBLIC_SETTINGS = (
    "mode",
    "paused",
    "analyst_mode",
    "notifier",
    "fomo_handle",
    "fees_ref_discount",
    "min_liq_usd",
    "max_top10_pct",
    "max_h1_pct",
    "min_age_sec",
    "waiting_room_max_sec",
    "min_lp_locked_pct",
    "insider_cluster_min",
    "alive_min_txns_m5",
    "alive_min_volume_m5",
    "organic_min_buy_ratio",
    "organic_max_buy_ratio",
    "flush_m5_pct",
    "chase_h1_pct",
    "min_score",
    "bankroll_usd",
    "risk_per_trade_pct",
    "max_position_usd",
    "min_position_usd",
    "max_open_positions",
    "stop_pct",
    "trail_pct",
    "trail_arm_pct",
    "timeout_sec",
)

# /set may change these. Mode goes through /paper and /live.
SETTABLE = (
    "paused",
    "fomo_handle",
    "fees_ref_discount",
    "min_liq_usd",
    "max_top10_pct",
    "max_h1_pct",
    "min_age_sec",
    "waiting_room_max_sec",
    "min_lp_locked_pct",
    "insider_cluster_min",
    "alive_min_txns_m5",
    "alive_min_volume_m5",
    "organic_min_buy_ratio",
    "organic_max_buy_ratio",
    "flush_m5_pct",
    "chase_h1_pct",
    "min_score",
    "bankroll_usd",
    "risk_per_trade_pct",
    "max_position_usd",
    "min_position_usd",
    "max_open_positions",
    "stop_pct",
    "trail_pct",
    "trail_arm_pct",
    "timeout_sec",
)

SECRET_ENV = (
    "TYPESAFE_API_KEY",
    "TG_TOKEN",
    "VAPID_PRIVATE_KEY",
    "APP_TOKEN",
    "DATA_PATH",
    "RPC_URL",
)

_SEGMENT = re.compile(r"[A-Za-z0-9_-]{32,128}")
_HANDLE = re.compile(r"[A-Za-z0-9_]{1,32}")


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def data_path_ok(value: str) -> bool:
    return bool(value) and bool(_SEGMENT.fullmatch(value))


class Config(BaseModel):
    typesafe_api_key: str = ""
    tg_token: str = ""
    vapid_private_key: str = ""
    app_token: str = ""
    data_path: str = ""
    rpc_url: str = ""
    pages_dir: str = "docs"
    pages_push: bool = False
    pages_push_sec: int = 60
    local_preview: bool = False
    local_host: str = "127.0.0.1"
    local_port: int = 8765
    db_path: str = ""
    root: str = ""

    def secret_values(self) -> set[str]:
        found = set()
        for value in (
            self.typesafe_api_key,
            self.tg_token,
            self.vapid_private_key,
            self.app_token,
            self.data_path,
            self.rpc_url,
        ):
            if value and len(value) >= 12:
                found.add(value)
        return found

    def snapshot_enabled(self) -> bool:
        return data_path_ok(self.data_path)


def load_config() -> Config:
    load_dotenv(ROOT / ".env")
    db_default = str(ROOT / "data" / "desk.sqlite")
    return Config(
        typesafe_api_key=os.environ.get("TYPESAFE_API_KEY", ""),
        tg_token=os.environ.get("TG_TOKEN", ""),
        vapid_private_key=os.environ.get("VAPID_PRIVATE_KEY", ""),
        app_token=os.environ.get("APP_TOKEN", ""),
        data_path=os.environ.get("DATA_PATH", ""),
        rpc_url=os.environ.get("RPC_URL", ""),
        pages_dir=os.environ.get("PAGES_DIR", "docs") or "docs",
        pages_push=os.environ.get("PAGES_PUSH", "0") == "1",
        pages_push_sec=int(os.environ.get("PAGES_PUSH_SEC", "60") or "60"),
        local_preview=os.environ.get("LOCAL_PREVIEW", "0") == "1",
        db_path=os.environ.get("DB_PATH", "") or db_default,
        root=str(ROOT),
    )


def validate_setting(key: str, value: str) -> str:
    """Return the stored string or raise ValueError. Refuses secret keys."""
    if key not in SETTABLE:
        raise ValueError(f"cannot set {key}")
    value = value.strip()
    if len(value) > 64:
        raise ValueError("value too long")
    if key == "paused":
        if value not in {"0", "1"}:
            raise ValueError("paused is 0 or 1")
        return value
    if key == "fomo_handle":
        handle = value.lstrip("@")
        if not _HANDLE.fullmatch(handle):
            raise ValueError("handle must be letters, numbers, underscore")
        return handle
    if key == "fees_ref_discount":
        number = float(value)
        if not 0 <= number <= 0.5:
            raise ValueError("discount must be between 0 and 0.5")
        return f"{number:.4f}".rstrip("0").rstrip(".") if "." in f"{number:.4f}" else value
    number = float(value)
    if key.endswith("_pct") or key.endswith("_ratio") or key in {"min_score", "fees_ref_discount"}:
        return str(number)
    if number < 0:
        raise ValueError("must be >= 0")
    if key in {"max_open_positions", "min_age_sec", "waiting_room_max_sec", "timeout_sec", "insider_cluster_min", "alive_min_txns_m5"}:
        return str(int(number))
    return str(number)
