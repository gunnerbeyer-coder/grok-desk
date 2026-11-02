import pytest

from desk.agents.executor import close_book, open_from_plan, plan_entry
from desk.agents.risk import advance_position
from desk.config import STEP_10_DEFAULTS
from desk.db import DB
from desk.links import coin_url


def test_stop_and_timeout_priority():
    held = advance_position(
        entry=1, stop=0.88, high=1, opened_at=0, deadline=1000,
        price=1.05, now=10, timeout_sec=3600, trail_pct=15, trail_arm_pct=10,
    )
    assert held.exit_reason is None
    assert held.stop == 0.88

    stopped = advance_position(
        entry=1, stop=0.88, high=1, opened_at=0, deadline=1000,
        price=0.88, now=10, timeout_sec=3600, trail_pct=15, trail_arm_pct=10,
    )
    assert stopped.exit_reason == "stop"
    assert stopped.exit_price == 0.88

    both = advance_position(
        entry=1, stop=0.88, high=1, opened_at=0, deadline=1000,
        price=0.5, now=5000, timeout_sec=3600, trail_pct=15, trail_arm_pct=10,
    )
    assert both.exit_reason == "stop"


def test_trail_ratchets_and_never_loosens():
    raised = advance_position(
        entry=1, stop=0.88, high=1, opened_at=0, deadline=10_000,
        price=1.2, now=10, timeout_sec=3600, trail_pct=15, trail_arm_pct=10,
    )
    assert raised.high == pytest.approx(1.2)
    assert raised.stop == pytest.approx(1.02)
    assert raised.exit_reason is None

    wider = advance_position(
        entry=1, stop=raised.stop, high=raised.high, opened_at=0, deadline=10_000,
        price=1.05, now=20, timeout_sec=3600, trail_pct=30, trail_arm_pct=10,
    )
    assert wider.stop == pytest.approx(raised.stop)
    assert wider.high == pytest.approx(1.2)
    assert wider.stop >= raised.stop


def test_timeout_can_tighten_and_cannot_extend():
    early = advance_position(
        entry=1, stop=0.88, high=1, opened_at=0, deadline=1000,
        price=1.0, now=100, timeout_sec=100, trail_pct=15, trail_arm_pct=50,
    )
    assert early.exit_reason == "timeout"
    not_yet = advance_position(
        entry=1, stop=0.88, high=1, opened_at=0, deadline=1000,
        price=1.0, now=999, timeout_sec=5000, trail_pct=15, trail_arm_pct=50,
    )
    assert not_yet.exit_reason is None
    due = advance_position(
        entry=1, stop=0.88, high=1, opened_at=0, deadline=1000,
        price=None, now=1000, timeout_sec=5000, trail_pct=15, trail_arm_pct=50,
    )
    assert due.exit_reason == "timeout"
    assert due.exit_price == 1


def test_database_refuses_a_lower_stop(tmp_path):
    db = DB(str(tmp_path / "book.sqlite"))
    db.init()
    settings = dict(STEP_10_DEFAULTS)
    plan = plan_entry(
        mode="paper",
        paused=False,
        price=1.0,
        equity=1000,
        cash=1000,
        open_count=0,
        recheck_ok=True,
        recheck_reason="ok",
        settings=settings,
        mint="MintStop",
    )
    signal_id = db.insert_signal("MintStop", 1.0, 80, "paper")

    class Ctx:
        pass

    ctx = Ctx()
    ctx.db = db
    open_from_plan(ctx, signal_id, plan, "paper")
    pos = db.position_for_mint("MintStop")
    db.update_stop(pos["id"], pos["stop"] - 0.5, pos["trail_high"])
    again = db.position_for_mint("MintStop")
    assert again["stop"] == pytest.approx(pos["stop"])
    db.update_stop(pos["id"], pos["stop"] + 0.05, pos["trail_high"] + 0.2)
    raised = db.position_for_mint("MintStop")
    assert raised["stop"] == pytest.approx(pos["stop"] + 0.05)
    assert raised["trail_high"] == pytest.approx(pos["trail_high"] + 0.2)
    # A manual close still prices the local book. No order id is created.
    result = close_book(ctx, raised, 1.1, "manual")
    assert result["pnl"] > 0
    assert db.position_for_mint("MintStop") is None
    assert coin_url("MintStop")
