"""Payload forensics shared by the live probes (coractions, shareholding, ...).

payload_shape answers "what did the exchange endpoint actually return?" in one
line — the probe output the parser is built from. parse_iso_day normalises the
two date spellings NSE payloads use ("26-Aug-2025", ISO) and RAISES on
anything else: a dated fact whose date can't be read is an error, never a
silently shifted row.
"""
from __future__ import annotations

from datetime import date as _date, datetime


def payload_shape(payload) -> str:
    """One-line shape report — what a probe output teaches the parser."""
    if isinstance(payload, dict):
        d = payload.get("data", payload)
        if isinstance(d, list):
            if not d:
                return "dict.data: empty list"
            if isinstance(d[0], dict):
                return f"dict.data: {len(d)} rows, keys={sorted(d[0])[:12]}"
            return f"dict.data: {len(d)} entries of {type(d[0]).__name__}"
        return f"dict keys={sorted(payload)[:12]}"
    if isinstance(payload, list):
        if not payload:
            return "empty list"
        if isinstance(payload[0], dict):
            return f"list: {len(payload)} rows, keys={sorted(payload[0])[:12]}"
        return f"list: {len(payload)} entries of {type(payload[0]).__name__}"
    return f"{type(payload).__name__}"


def parse_iso_day(d: str) -> _date:
    """"26-Aug-2025" or ISO -> datetime.date. Raises on anything else."""
    s = str(d).strip()
    try:
        return _date.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {s!r}")
