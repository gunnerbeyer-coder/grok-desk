"""FOMO ordinary-Solana spot fee schedule.

Published bands (FOMO help center, compiled 2026-09-18):

- [0, 5) USDC: flat 0.10
- [5, 47.50): 2%
- [47.50, 190): flat 0.95
- [190, inf): 0.50%

The referral article quotes the discounted rate on the top band only:
0.50% times (1 - fees_ref_discount). At 0.10 that is 0.45%. Smaller bands
stay on the published flat or percentage amount.

The desk fills paper trades at the observed market price, so the pool fee
is already inside that price. FOMO covers network fees on its gasless
route, so this model adds no priority-fee estimate. The figure FOMO shows
before a person confirms a swap is the fee that actually applies. This
module only prices the local paper book.
"""

from __future__ import annotations

# (lower inclusive, upper exclusive or None, kind, value)
_BANDS = (
    (0.0, 5.0, "flat", 0.10),
    (5.0, 47.50, "pct", 0.02),
    (47.50, 190.0, "flat", 0.95),
    (190.0, None, "pct", 0.005),
)

_TOP_BAND_RATE = 0.005


def fomo_solana_fee(notional_usd: float, ref_discount: float = 0.10) -> float:
    """FOMO fee in USDC for one Solana spot leg."""
    notional = float(notional_usd or 0)
    if notional <= 0:
        return 0.0
    discount = min(0.9, max(0.0, float(ref_discount)))
    for lower, upper, kind, value in _BANDS:
        if notional < lower:
            continue
        if upper is not None and notional >= upper:
            continue
        if kind == "flat":
            return float(value)
        rate = float(value)
        if value == _TOP_BAND_RATE:
            rate = rate * (1.0 - discount)
        return notional * rate
    return 0.0


def round_trip_fee(entry_notional: float, exit_notional: float, ref_discount: float = 0.10) -> float:
    return fomo_solana_fee(entry_notional, ref_discount) + fomo_solana_fee(
        exit_notional, ref_discount
    )
