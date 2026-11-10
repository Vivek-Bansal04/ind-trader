"""Telegram alerts — one-way evening briefing to your phone.

Deliberately ONE-WAY: we never poll getUpdates, so the bot cannot be a control
channel from a phone into the system. Token/chat come from env only. Sending is
best-effort: any failure prints and returns (False, reason), never raises.
"""
from __future__ import annotations

import time

import httpx

from indtrader.config import Settings

API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = 15.0


def is_configured(s: Settings) -> bool:
    return bool(s.telegram_bot_token.strip() and s.telegram_chat_id.strip())


def inr(x: float) -> str:
    """Compact Indian money: +14.1Cr / -3.1L / ₹9,500."""
    ax = abs(float(x))
    sign = "+" if x > 0 else ("-" if x < 0 else "")
    if ax >= 1e7:
        return f"{sign}{ax / 1e7:,.1f}Cr"
    if ax >= 1e5:
        return f"{sign}{ax / 1e5:,.1f}L"
    return f"{sign}{ax:,.0f}"


def send(text: str, s: Settings) -> tuple[bool, str]:
    """Plain-text sendMessage (no Markdown — symbols like RELIANCE^ break parsers).
    One polite retry on 429. Never raises."""
    if not is_configured(s):
        return False, "not configured (set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)"
    payload = {"chat_id": s.telegram_chat_id.strip(), "text": text[:4000],
               "disable_web_page_preview": True}
    url = API.format(token=s.telegram_bot_token.strip())
    try:
        r = httpx.post(url, json=payload, timeout=TIMEOUT)
        if r.status_code == 429:  # rate limited — wait the asked amount, once
            wait = min(float(r.json().get("parameters", {}).get("retry_after", 3)), 10.0)
            time.sleep(wait)
            r = httpx.post(url, json=payload, timeout=TIMEOUT)
        if r.status_code == 200 and r.json().get("ok"):
            return True, "sent"
        return False, f"http {r.status_code}: {r.text[:120]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
