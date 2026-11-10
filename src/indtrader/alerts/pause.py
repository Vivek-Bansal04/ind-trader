"""Quiet-switch for the outbound loop (the plan's "pause trading", translated: there
is deliberately NO executor here, so the thing to pause is the daily notify/ETL loop).

A flag file in DATA_DIR — survives restarts, visible with `dir`, no schema to trust.
"""
from __future__ import annotations


def flag_path(settings=None):
    from indtrader.config import get_settings

    s = settings or get_settings()
    return s.data_dir / "paused.flag"


def is_paused(settings=None) -> bool:
    return flag_path(settings).exists()


def set_paused(on: bool, reason: str = "", settings=None) -> bool:
    p = flag_path(settings)
    if on:
        p.write_text(reason or "paused", encoding="utf-8")
        return True
    if p.exists():
        p.unlink()
    return False
