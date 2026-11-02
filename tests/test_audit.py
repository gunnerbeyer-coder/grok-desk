import base64
import struct

from desk.agents.auditor import audit_layers, cluster_size, parse_gpa_slice
from desk.config import STEP_10_DEFAULTS
from desk.fields import TOKEN_2022_PROGRAM, TOKEN_PROGRAM, b58encode
from desk.vendors import gpa_params


def _good():
    return {
        "mint_authority_known": True,
        "mint_authority": None,
        "freeze_known": True,
        "freeze_authority": None,
        "token_program": TOKEN_2022_PROGRAM,
        "pair_address": "PAIR",
        "markets": [{"pubkey": "PAIR", "marketType": "pump_fun_amm", "lpLockedPct": 100}],
        "holders": [
            {"address": "vault", "owner": "PAIR", "pct": 40, "insider": False},
            {"address": "a", "owner": "w1", "pct": 2, "insider": False},
            {"address": "b", "owner": "w2", "pct": 1.5, "insider": False},
        ],
        "graph_ok": True,
        "graph": [],
        "gpa_ok": True,
        "supply": 1000,
        "amounts": [20, 15, 10, 5],
    }


def test_audit_stops_at_the_first_red_and_passes_eight():
    settings = dict(STEP_10_DEFAULTS)
    red = _good()
    red["mint_authority"] = "still-set"
    layers = audit_layers(red, settings)
    assert len(layers) == 1
    assert layers[0]["status"] == "red"
    assert layers[0]["name"] == "mint_authority"

    passed = audit_layers(_good(), settings)
    assert len(passed) == 8
    assert all(layer["status"] == "pass" for layer in passed)
    assert passed[-1]["name"] == "program_accounts"


def test_graph_reads_links_not_edges():
    linked = {
        "nodes": [{"id": f"w{i}", "holdings": 1} for i in range(5)],
        "links": [{"source": f"w{i}", "target": f"w{i+1}"} for i in range(4)],
    }
    assert cluster_size([linked]) == 5
    edges_only = {
        "nodes": [{"id": f"w{i}", "holdings": 1} for i in range(5)],
        "edges": [{"source": f"w{i}", "target": f"w{i+1}"} for i in range(4)],
    }
    assert cluster_size([edges_only]) == 0
    info = _good()
    info["graph"] = [linked]
    layers = audit_layers(info, dict(STEP_10_DEFAULTS))
    assert layers[-1]["name"] == "insider_graph"
    assert layers[-1]["status"] == "red"
    assert len(layers) == 7


def test_program_accounts_target_both_token_programs():
    assert TOKEN_2022_PROGRAM == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
    assert TOKEN_PROGRAM == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    modern = gpa_params(TOKEN_2022_PROGRAM, "Mint")
    assert modern[0] == TOKEN_2022_PROGRAM
    assert modern[1]["dataSlice"] == {"offset": 32, "length": 40}
    assert not any("dataSize" in filt for filt in modern[1]["filters"])
    classic = gpa_params(TOKEN_PROGRAM, "Mint")
    assert {"dataSize": 165} in classic[1]["filters"]
    assert {"memcmp": {"offset": 0, "bytes": "Mint"}} in classic[1]["filters"]


def test_gpa_slice_and_base58():
    assert b58encode(b"\x00" * 32) == "1" * 32
    owner = b"\x02" + b"\x11" * 31
    amount = 99
    raw = owner + struct.pack("<Q", amount)
    encoded = base64.b64encode(raw).decode()
    got_owner, got_amount = parse_gpa_slice(encoded)
    assert got_owner == b58encode(owner)
    assert got_amount == 99
