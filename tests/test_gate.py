from desk.agents.analyst import evaluate
from desk.agents.scanner import (
    KILL_ORDER,
    is_copycat,
    parse_migration,
    pumpswap_pair,
    screen_decision,
)
from desk.config import STEP_10_DEFAULTS


def _settings():
    return dict(STEP_10_DEFAULTS)


def _screen(**overrides):
    base = {
        "liq_usd": 50_000,
        "top10_pct": 10,
        "h1_pct": 10,
        "copycat": False,
        "age_s": 300,
    }
    base.update(overrides)
    return base


def test_kill_order_is_liq_then_top10_then_h1_then_copycat():
    assert KILL_ORDER == ("liq", "top10", "h1", "copycat")
    settings = _settings()
    assert screen_decision(
        _screen(liq_usd=39_999, top10_pct=80, h1_pct=200, copycat=True),
        settings,
        0,
    ) == "kill:liq"
    assert screen_decision(_screen(top10_pct=25.01, h1_pct=200), settings, 0) == "kill:top10"
    assert screen_decision(_screen(h1_pct=80.01, copycat=True), settings, 0) == "kill:h1"
    assert screen_decision(_screen(h1_pct=80, copycat=True), settings, 0) == "kill:copycat"


def test_boundaries_pass_and_waiting_room():
    settings = _settings()
    assert screen_decision(_screen(liq_usd=40_000, top10_pct=25, h1_pct=80), settings, 0) == "pass"
    assert screen_decision(_screen(age_s=179), settings, 0) == "wait"
    assert screen_decision(_screen(age_s=180), settings, 0) == "pass"
    assert screen_decision(_screen(liq_usd=None, top10_pct=None), settings, 0) == "wait"
    assert screen_decision(_screen(liq_usd=None, top10_pct=None), settings, 900) == "kill:incomplete"
    assert screen_decision(_screen(age_s=10), settings, 900) == "kill:stale"


def test_copycat_and_pool_type_is_not_an_address():
    assert is_copycat("BONK", "dog", set())
    assert is_copycat("BONK2", "dog", set())
    assert is_copycat("WOW", "wow", {"WOW"})
    assert not is_copycat("MEOW", "cat", set())
    parsed = parse_migration(
        {"mint": "Mint", "signature": "Sig", "txType": "migrate", "pool": "raydium-cpmm"}
    )
    assert parsed["pool_type"] == "raydium-cpmm"
    assert "pair_address" not in parsed
    parsed = parse_migration(
        {"mint": "Mint", "pool": "HcGPz2eXV8SFcMVQ9fJPBd71bRLJVzUtd2avvv86GwbK"}
    )
    assert parsed["pool_type"].startswith("HcGP")
    assert "pair_address" not in parsed


def test_only_pumpswap_pairs():
    pairs = [
        {"dexId": "raydium", "pairAddress": "RAY", "liquidity": {"usd": 999999}, "baseToken": {"address": "M"}},
        {"dexId": "pumpswap", "pairAddress": "PUMP", "liquidity": {"usd": 42000}, "baseToken": {"address": "M"}},
    ]
    chosen = pumpswap_pair(pairs)
    assert chosen["pairAddress"] == "PUMP"
    assert chosen["dexId"] == "pumpswap"


def _healthy(**overrides):
    metrics = {
        "buys_m5": 10,
        "sells_m5": 6,
        "volume_m5": 20000,
        "liquidity_usd": 80000,
        "price_change_m5": 2,
        "price_change_h1": 10,
        "price_usd": 0.001,
    }
    metrics.update(overrides)
    return metrics


def test_gate_passes_a_healthy_print():
    result = evaluate(_healthy(), _settings())
    assert result["alive"] and result["organic"]
    assert not result["flush"] and not result["chase"]
    assert result["score"] >= 60
    assert result["gate"] is True
    assert result["reason"] == "pass"


def test_gate_blocks_flush_chase_dead_and_one_sided():
    settings = _settings()
    flush = evaluate(_healthy(price_change_m5=-12), settings)
    assert flush["flush"] and flush["gate"] is False and "flush" in flush["reason"]
    chase = evaluate(_healthy(price_change_h1=40), settings)
    assert chase["chase"] and chase["gate"] is False
    dead = evaluate(_healthy(buys_m5=2, sells_m5=1, volume_m5=100), settings)
    assert dead["alive"] is False and dead["gate"] is False
    sided = evaluate(_healthy(buys_m5=20, sells_m5=0), settings)
    assert sided["organic"] is False and sided["gate"] is False


def test_gate_blocks_a_low_score_and_refuses_jev():
    settings = _settings()
    quiet = evaluate(
        {
            "buys_m5": 5,
            "sells_m5": 3,
            "volume_m5": 1000,
            "liquidity_usd": 40000,
            "price_change_m5": 0,
            "price_change_h1": 30,
        },
        settings,
    )
    assert quiet["alive"] and quiet["organic"]
    assert quiet["score"] < 60
    assert quiet["gate"] is False and "score" in quiet["reason"]
    settings["analyst_mode"] = "jev"
    refused = evaluate(_healthy(), settings)
    assert refused["gate"] is False
    assert refused["reason"] == "analyst_mode"
