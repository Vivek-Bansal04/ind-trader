"""Read-only broker adapters + holdings reconciliation.

Hard rules, in code:
- NO order endpoints exist in this package. Adapters hold read-scoped credentials
  (Fyers: user-generated app with API enabled; Angel: TOTP-based login is Phase-2).
- Credentials come from env only; nothing here can place or modify an order, and
  the reconciliation layer treats broker data as ground truth for holdings and the
  journal as ground truth for INTENT — a mismatch is a report, never an action.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

TIMEOUT = 20.0


@dataclass(frozen=True)
class Holding:
    symbol: str
    qty: int
    avg_price: float
    ltp: float


class FyersReadOnly:
    """Fyers v3 REST, read-only. Needs FYERS_ACCESS_TOKEN (generated in the Fyers
    web/dashboard after app creation; valid for the whole day)."""

    name = "fyers"
    BASE = "https://api-t1.fyers.in/api/v3"

    def __init__(self, access_token: str | None = None):
        if access_token is None:
            from indtrader.config import get_settings

            access_token = get_settings().fyers_access_token or os.environ.get("FYERS_ACCESS_TOKEN", "")
        self.token = access_token

    def available(self) -> bool:
        return bool(self.token)

    def holdings(self) -> list[Holding]:
        if not self.available():
            raise RuntimeError("FYERS_ACCESS_TOKEN not set — read-only token from Fyers dashboard")
        r = httpx.get(
            f"{self.BASE}/data/holdings",
            headers={"Authorization": f"{self.token}", "accept": "application/json"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return parse_fyers_holdings(r.json())


def parse_fyers_holdings(payload: dict) -> list[Holding]:
    """Pure parser (offline-testable). Fyers v3 holdings envelope:
    {'s': 'ok', 'd': {'net': [{symbol, netQty, netAvgPrice, ltp, ...}]}}"""
    if not isinstance(payload, dict):
        return []
    if str(payload.get("s", "")).lower() != "ok":
        raise RuntimeError(f"fyers error envelope: {str(payload)[:150]}")
    rows = (payload.get("d") or {}).get("net") or []
    out = []
    for r in rows:
        try:
            qty = int(float(r["netQty"]))
            if qty == 0:
                continue
            symbol = str(r["symbol"]).upper()
            if ":" in symbol:
                symbol = symbol.split(":", 1)[1]          # "NSE:RELIANCE-EQ" -> "RELIANCE-EQ"
            if symbol.endswith("-EQ"):
                symbol = symbol[:-3]                       # -> "RELIANCE" (keeps hyphens in BAJAJ-AUTO)
            out.append(
                Holding(symbol=symbol, qty=qty, avg_price=float(r.get("netAvgPrice", 0) or 0),
                        ltp=float(r.get("ltp", 0) or 0))
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out
