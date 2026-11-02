import pytest

from desk.agents.executor import (
    PAPER_PREFIX,
    close_book,
    fit_size,
    open_from_plan,
    paper_fill_price,
    plan_entry,
    sell_text,
    signal_text,
)
from desk.config import STEP_10_DEFAULTS
from desk.db import DB
from desk.fees import fomo_solana_fee
from desk.links import coin_url


def _plan(**overrides):
    args = dict(
        mode="paper",
        paused=False,
        price=0.001,
        equity=1000,
        cash=1000,
        open_count=0,
        recheck_ok=True,
        recheck_reason="ok",
        settings=dict(STEP_10_DEFAULTS),
        mint="MintExec",
    )
    args.update(overrides)
    return plan_entry(**args)


def test_paper_fills_at_the_signal_price_and_live_waits():
    plan = _plan()
    assert plan["action"] == "fill"
    assert plan["fill_price"] == paper_fill_price(0.001) == 0.001
    assert plan["size_usd"] == pytest.approx(100)
    assert plan["entry_fee"] == pytest.approx(fomo_solana_fee(100, 0.10))
    assert plan["coin_url"] == coin_url("MintExec")
    assert plan["stop"] == pytest.approx(0.001 * 0.88)
    assert "order_id" not in plan
    live = _plan(mode="live")
    assert live["action"] == "await"
    assert live["fill_price"] is None
    assert _plan(paused=True)["reason"] == "paused"
    assert _plan(recheck_ok=False, recheck_reason="liq")["reason"] == "liq"
    assert _plan(open_count=3)["reason"] == "capacity"
    assert _plan(price=0)["reason"] == "no_price"


def test_size_respects_cash_and_minimum():
    size, fee = fit_size(1000, 100, 0.10, 15)
    assert size == pytest.approx(100)
    # $100 sits in the published flat band, so the top-band discount does not apply.
    assert fee == pytest.approx(0.95)
    assert fit_size(10, 100, 0.10, 15) == (0.0, 0.0)


def test_paper_prefix_and_local_fill(tmp_path):
    assert signal_text("paper", "CAT", 0.1, 40, 0.08).startswith(PAPER_PREFIX)
    assert not signal_text("live", "CAT", 0.1, 40, 0.08).startswith("PAPER")
    assert sell_text("paper", "CAT", 0.1, "stop", 1.2).startswith(PAPER_PREFIX)

    db = DB(str(tmp_path / "book.sqlite"))
    db.init()
    plan = _plan()
    signal_id = db.insert_signal("MintExec", 0.001, 88, "paper")

    class Ctx:
        pass

    ctx = Ctx()
    ctx.db = db
    position_id = open_from_plan(ctx, signal_id, plan, "paper")
    assert position_id
    assert db.signal(signal_id)["status"] == "filled"
    cash = float(db.get_setting("cash"))
    assert cash == pytest.approx(1000 - plan["size_usd"] - plan["entry_fee"])
    pos = db.position_for_mint("MintExec")
    assert pos["entry_price"] == pytest.approx(0.001)
    assert pos["mode"] == "paper"
    result = close_book(ctx, pos, 0.0012, "stop")
    # Exit notional is $120, still inside the $0.95 flat band.
    assert result["pnl"] == pytest.approx(18.10)
    assert float(db.get_setting("cash")) == pytest.approx(1018.10)
    assert open_from_plan(ctx, signal_id, plan, "paper") is None
