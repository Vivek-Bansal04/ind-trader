"""Grounding-evals: prompt-contract checklist (concept from the claude-skills audit —
"make 'AI never decides' tested, not just intended"). These run fully offline: they
check the CONTRACT we send to any model, not a model's answer."""
import json

from indtrader.ai.prompts import SCHEMA_KEYS, SYSTEM, build_prompt
from indtrader.ai.router import parse_insight

FACTPACK = {
    "symbol": "TATASTEEL", "date": "2026-11-07", "close": 168.4,
    "rsi14": 61.2, "sma50": 160.1, "sma200": 152.7, "ret_6m": 0.11,
    "atr14": 3.1, "annvol": 0.27, "quadrant": "Leading", "regime": "ON",
}


def test_system_pins_horizon_and_instruments():
    low = SYSTEM.lower()
    for must in ("daily/weekly", "delivery", "no intraday", "no options"):
        assert must in low


def test_system_grounding_rules_present():
    low = SYSTEM.lower()
    assert "never invent" in low                      # no fabricated numbers
    assert "not available" in low                     # missing field -> say unknown
    assert "data date" in low                         # stale close must be labelled


def test_schema_keys_are_all_declared_in_the_json_contract():
    for key in SCHEMA_KEYS:
        assert key in SYSTEM, f"{key} parsed but not demanded in SYSTEM"


def test_prompt_states_data_date_and_hold_path():
    p = build_prompt(FACTPACK)
    assert "Date: 2026-11-07" in p
    assert "HOLD" in p                                # weak evidence has an exit
    assert "NON-INTRADAY" in p


def test_missing_optional_field_prints_none_loaded_not_blank():
    p = build_prompt({k: v for k, v in FACTPACK.items() if k != "announcements"})
    assert "none loaded" in p                         # unknown != zero


def test_parser_keeps_falsifier_field():
    raw = json.dumps({"verdict": "HOLD", "confidence": 55, "summary": "mixed",
                      "risks": "breadth", "what_would_change_the_view": "close below SMA200"})
    ins = parse_insight("fake", raw)
    assert ins.verdict == "HOLD" and ins.watch_for == "close below SMA200"


def test_parser_missing_falsifier_is_empty_not_invented():
    raw = json.dumps({"verdict": "BUY", "confidence": 70, "summary": "s", "risks": "r"})
    ins = parse_insight("fake", raw)
    assert ins.watch_for == ""
