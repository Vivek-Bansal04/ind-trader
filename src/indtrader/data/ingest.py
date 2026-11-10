"""Broker tradebook / contract-note CSV import (plan §A: seed ledger history).

One-shot import of a broker CSV into the fills ledger. Broker export shapes vary
and change without notice, so this is CONTRACT-FIRST by explicit column mapping:
the caller states which CSV column carries each field (or uses one of the shipped
FORMATS, which are mappings we have actually seen), and a row that violates the
contract ABORTS the import naming the row — a half-imported ledger is worse than
none, because FIFO P&L and the tax sheet inherit every silent mistake.

Money rule: costs stay 0 unless a column is mapped for them — imported rows are
net-price fills; the tax ledger treats 0 costs honestly (it will understate the
basis, and `tax` output already says computation-only).
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from indtrader.portfolio.tax import LedgerEntry

FIELDS = ("dt", "symbol", "side", "qty", "price", "costs")
SIDES = {"BUY": "BUY", "B": "BUY", "BOUGHT": "BUY",
         "SELL": "SELL", "S": "SELL", "SOLD": "SELL"}
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y",
                "%Y-%m-%dT%H:%M:%S", "%d/%m/%y")

# Mappings for exports we have actually seen; extend, never guess silently.
FORMATS: dict[str, dict[str, str]] = {
    "generic": {"dt": "date", "symbol": "symbol", "side": "side",
                "qty": "qty", "price": "price", "costs": "costs"},
    "zerodha-tradebook": {"dt": "date", "symbol": "symbol", "side": "transaction_type",
                          "qty": "quantity", "price": "price"},
    "angel": {"dt": "trade_date", "symbol": "symbol", "side": "transaction_type",
              "qty": "quantity", "price": "avg_price"},
}


def _parse_dt(v: str, row_no: int) -> date:
    v = str(v).strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(v, f).date()
        except ValueError:
            continue
    raise ValueError(f"row {row_no}: unparseable date {v!r} (formats tried: {DATE_FORMATS})")


def parse_trade_csv(path: str | Path, fmt: str = "generic",
                    column_map: dict[str, str] | None = None) -> list[LedgerEntry]:
    """CSV -> LedgerEntry list. Refuses (ValueError, naming the row/column) on any
    contract violation; never imports a partial batch."""
    if fmt not in FORMATS and not column_map:
        raise ValueError(f"unknown format {fmt!r}; known: {sorted(FORMATS)} or pass column_map")
    mapping = {**FORMATS.get(fmt, {}), **(column_map or {})}
    missing = [f for f in ("dt", "symbol", "side", "qty", "price") if f not in mapping]
    if missing:
        raise ValueError(f"column_map must cover {missing} (costs optional); have {sorted(mapping)}")

    out: list[LedgerEntry] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        absent = [c for f, c in mapping.items() if c not in header and f != "costs"]
        if absent:
            raise ValueError(f"mapped columns absent from file: {absent}; file columns: {header}")
        if "costs" in mapping and mapping["costs"] not in header:
            mapping.pop("costs")  # optional: absent costs column means zero-cost fills
        for no, row in enumerate(reader, start=2):
            try:
                qty = float(str(row[mapping["qty"]]).replace(",", ""))
                price = float(str(row[mapping["price"]]).replace(",", ""))
                costs = float(str(row[mapping["costs"]]).replace(",", "")) if "costs" in mapping else 0.0
                side = SIDES.get(str(row[mapping["side"]]).strip().upper())
                sym = str(row[mapping["symbol"]]).strip().upper()
                if not sym or side is None or qty <= 0 or price <= 0 or costs < 0:
                    raise ValueError("empty/invalid field")
                out.append(LedgerEntry(_parse_dt(row[mapping["dt"]], no), sym, side, qty, price, costs))
            except ValueError as e:
                raise ValueError(f"row {no} rejected: {e} — row: {row}") from None
    if not out:
        raise ValueError("file matched the contract but contained zero rows — wrong file?")
    return out


def import_into_log(entries: list[LedgerEntry], log, note: str = "csv-import") -> int:
    """Insert as ledger fills. Duplicate guard: the same (dt,symbol,side,qty,price)
    already in the ledger is skipped and counted, so re-running is safe."""
    have = {(str(f.dt), f.symbol.upper(), f.side.upper(), round(float(f.qty), 4),
             round(float(f.price), 4)) for f in log.fills()}
    n = 0
    for e in entries:
        key = (str(e.dt), e.symbol.upper(), e.side.upper(), round(float(e.qty), 4),
               round(float(e.price), 4))
        if key in have:
            continue
        log.add_fill(e.dt, e.symbol, e.side, e.qty, e.price, e.costs, note)
        have.add(key)
        n += 1
    return n
