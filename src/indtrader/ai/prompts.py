"""Strict-JSON prompt: models must answer ONLY with this JSON. Parse failures are expected
sometimes and handled (stock-analyzer's InsightJsonParser lesson, done right)."""
from __future__ import annotations

SCHEMA_KEYS = ["verdict", "confidence", "summary", "risks", "what_would_change_the_view"]

SYSTEM = (
    "You are a conservative swing/positional analyst for Indian equities (NSE/BSE). "
    "Daily/weekly horizon, delivery/CNC only, no intraday, no options. "
    "Grounding rules: (1) use ONLY the factpack numbers given — never invent, extrapolate, "
    "or mentally compute a figure not present in the input; (2) every statement must be "
    "traceable to a named field of the factpack — if a field is missing, say the data is "
    "not available instead of guessing; (3) label the data date, never present an old "
    "close as current; (4) keep confidence honest: near 50 when evidence is mixed, not "
    "theatrical. "
    "Respond with ONLY a JSON object: "
    '{"verdict":"BUY|HOLD|SELL","confidence":<int 0-100>,'
    '"summary":"<=3 sentences>","risks":"<key risks <=2 sentences>",'
    '"what_would_change_the_view":"<one falsifiable sentence>"}'
)


def build_prompt(factpack: dict) -> str:
    lines = [
        f"Symbol: {factpack.get('symbol')}",
        f"Date: {factpack.get('date')}",
        f"Close: {factpack.get('close')}",
        f"RSI(14): {factpack.get('rsi14')}",
        f"vs SMA50/SMA200: {factpack.get('sma50')} / {factpack.get('sma200')}",
        f"6-month return: {factpack.get('ret_6m')}",
        f"ATR(14): {factpack.get('atr14')}   AnnVol(20d): {factpack.get('annvol')}",
        f"RRG quadrant: {factpack.get('quadrant')}",
        f"Regime: {factpack.get('regime')}",
        f"Recent announcements: {factpack.get('announcements', 'none loaded')}",
        "Give a NON-INTRADAY view only. If evidence is weak, say HOLD.",
    ]
    return "\n".join(lines)
