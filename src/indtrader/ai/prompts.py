"""Strict-JSON prompt: models must answer ONLY with this JSON. Parse failures are expected
sometimes and handled (stock-analyzer's InsightJsonParser lesson, done right)."""
from __future__ import annotations

SCHEMA_KEYS = ["verdict", "confidence", "summary", "risks"]

SYSTEM = (
    "You are a conservative swing/positional analyst for Indian equities (NSE/BSE). "
    "Daily/weekly horizon, delivery/CNC only, no intraday, no options. Never invent data. "
    "Respond with ONLY a JSON object: "
    '{"verdict":"BUY|HOLD|SELL","confidence":<int 0-100>,'
    '"summary":"<=3 sentences>","risks":"<key risks <=2 sentences>"}'
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
