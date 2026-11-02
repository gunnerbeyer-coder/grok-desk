"""Shared handles passed into every long-running task."""

from __future__ import annotations

from dataclasses import dataclass

from desk.bus import Bus
from desk.config import Config
from desk.db import DB
from desk.vendors import Vendors


@dataclass
class Desk:
    cfg: Config
    db: DB
    bus: Bus
    vendors: Vendors
