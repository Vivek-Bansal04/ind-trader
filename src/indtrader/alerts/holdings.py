"""Human-recorded holdings + the two watchers a holder actually asks for.

The journal is the single source of truth: a holding recorded on the dashboard
is the SAME ledger row `indtrader fill` writes — no parallel portfolio store,
so XIRR, tax, reconcile and the news feed all see it without extra wiring.
This module is the ONLY write path the dashboard exposes, and it is a journal
entry, never an order: nothing here can touch a broker, a network call that
changes anything, or the order-free constitution.

Entry price and P&L come from the SAME FIFO walk the tax engine uses
(portfolio.tax.open_lots): sells consume the oldest lots, so the average cost
of what is LEFT is the real basis — no separate book to drift.

The watchers (run by the evening loop, also callable on demand via
`indtrader holdings --notify`):
  1. results countdown — Telegram when a HELD name's stored quarterly-results
     date is within RESULTS_ALERT_DAYS. A held name with no stored date is
     PRINTED as unknown (never silently skipped) and listed in the return dict.
  2. good news — Telegram when a stored headline on a held name matches the
     GREEN keyword list AND survives the existing RED/AMBER triage (a headline
     that reads both ways ships as the warning, never the "good news" spin).

Both send AT MOST ONCE per event: a signals row keyed on the exact message
text is written only after a successful send — a failed send re-fires tomorrow,
a sent one never repeats.
"""
from __future__ import annotations

import re

DASHBOARD_NOTE = "recorded via dashboard"
RESULTS_ALERT_DAYS = 4  # user-chosen "3-4 days prior"; fires at <= 4 calendar days
FILL_NOTE_MAX = 200
SYM_RE = re.compile(r"^[A-Z0-9&.\-]{1,20}$")

# positive-headline vocabulary, same word-boundary/case-insensitive machinery as
# announce.triage_title. Deliberately narrow: "record" and "launch" are why plain
# substrings can't be trusted; the GREEN-only match still runs through RED/AMBER
# first via good_news().
GREEN = (
    "record high", "record profit", "best quarter", "beat estimates", "beats estimates",
    "beats market expectation", "strong results", "strong growth",
    "net profit jumps", "profit jumps", "profit surges", "profit rises", "profit spikes",
    "net profit surges", "earnings beat",
    "contract win", "wins order", "won order", "bagged order", "bags order", "orders win",
    "new contract", "orders worth", "order book", "big ticket", "deal win", "clinches",
    "secures order", "export win", "market share gain", "capacity expansion",
    "commissioned", "buyback", "dividend", "special dividend", "stock split",
    "bonus issue", "bought back", "stake upgrade", "weight upgrade",
    "target price raised", "target raised", "upgraded to", "major customer",
    "strategic partnership", "joint venture", "capacity doubled", "record quarter",
    "record results", "double-digit growth", "double digit growth",
)


def record_holding(log, symbol: str, qty, price, dt=None, costs=0.0, note: str = "") -> int:
    """One validated human-confirmed BUY fill -> the SAME ledger `fill` writes.
    Every refusal names itself; a row only lands when symbol/qty/price/date all
    pass. Returns the fill id."""
    from datetime import date

    from indtrader.data.payloads import parse_iso_day

    sym = str(symbol or "").strip().upper()
    if not SYM_RE.match(sym):
        raise ValueError(f"bad symbol {symbol!r} — letters/digits/&/./- (exchange ticker), nothing else")
    try:
        qf = float(qty)
    except (TypeError, ValueError):
        raise ValueError(f"qty must be a number of shares, got {qty!r}")
    if qf <= 0 or qf != int(qf):
        raise ValueError(f"qty must be a positive whole number of shares, got {qty!r}")
    q = int(qf)
    try:
        px = float(price)
    except (TypeError, ValueError):
        raise ValueError(f"price must be a number (your actual purchase price), got {price!r}")
    if not px > 0:
        raise ValueError(f"price must be positive, got {px}")
    try:
        c = float(costs or 0.0)
    except (TypeError, ValueError):
        raise ValueError(f"costs must be a number (brokerage+taxes for this fill), got {costs!r}")
    if c < 0:
        raise ValueError(f"costs cannot be negative, got {c}")
    when = date.today() if dt in (None, "") else None
    if when is None:
        try:
            when = parse_iso_day(str(dt))
        except ValueError:
            raise ValueError(f"bad date {dt!r} — want YYYY-MM-DD (or leave empty for today)")
    note = " ".join(str(note or "").split())[:FILL_NOTE_MAX]
    return log.add_fill(when, sym, "BUY", q, px, c,
                        ((note + " ") if note else "") + DASHBOARD_NOTE)


def holdings_view(store, log) -> dict:
    """The My-holdings panel: per held name — qty, FIFO average entry (open lots
    only, so partial sells re-price the entry honestly), last close + date, value,
    P&L ₹ and %, 60-day sparkline, results countdown or the honest unknown, and
    the name's latest stored news items (triage severity shown when flagged).
    No bars -> close is None and the row says so; a missing results date is
    'unknown', never a fake 'far away'."""
    from datetime import date

    import pandas as pd

    from indtrader.portfolio.tax import open_lots
    from indtrader.portfolio.valuation import held_quantities, resolve_marks
    from indtrader.research import events
    from indtrader.web.dashboard import sparkline

    fills = log.fills()
    held = held_quantities(fills)
    marks = resolve_marks(store, held)
    lots = open_lots(fills) if fills else []
    basis: dict[str, list] = {}
    for lot in lots:  # remaining FIFO lots only: qty>0 guaranteed by open_lots
        b = basis.setdefault(lot.symbol.upper(), [0.0, 0.0])
        b[0] += lot.qty * lot.price
        b[1] += lot.qty
    today = date.today()
    rows = []
    for sym in sorted(held):
        qty = held[sym]
        cost = round(basis[sym][0] / basis[sym][1], 2) if basis.get(sym) and basis[sym][1] else None
        m = marks.get(sym)
        value = round(qty * m, 0) if m is not None else None
        pnl = round(qty * (m - cost), 0) if (m is not None and cost is not None) else None
        pnl_pct = (m / cost - 1.0) if (m is not None and cost) else None
        df = store.load(sym)
        last_d = str(pd.Timestamp(df.index[-1]).date()) if df is not None and not df.empty else None
        ev = events.next_event(store, sym)
        if ev:
            days = (pd.Timestamp(ev) - pd.Timestamp(today)).days
            results = f"{ev} (in {days} day{'s' if days != 1 else ''})" if days >= 0 \
                else f"passed {ev}"
        elif str(sym).upper().startswith("MF:"):
            results = "n/a — a fund, no quarterly results"  # unknown = never "n/a" for stocks
        else:
            results = "date unknown — run: indtrader events"
        news = _news_for(store, sym)
        rows.append({"symbol": sym, "qty": round(qty, 4), "avg": cost, "close": m,
                     "close_date": last_d, "value": value, "pnl": pnl,
                     "pnl_pct": None if pnl_pct is None else round(pnl_pct, 4),
                     "spark": {"svg": sparkline(df.tail(60)["close"])} if df is not None and not df.empty else None,
                     "results": results, "news": news})
    tot_v = [r["value"] for r in rows]
    total_value = round(sum(tot_v), 0) if all(v is not None for v in tot_v) and tot_v else None
    tot_p = [r["pnl"] for r in rows]
    total_pnl = round(sum(tot_p), 0) if all(v is not None for v in tot_p) and tot_p else None
    return {"rows": rows, "total_value": total_value, "total_pnl": total_pnl,
            "empty_ok": not rows,
            "hint": ("nothing recorded yet — add the stocks you already own below; "
                     "this is the SAME journal `indtrader fill` writes, not a parallel list")
            if not rows else ""}


def _news_for(store, sym: str, days: int = 7, cap: int = 4) -> list:
    from datetime import date, timedelta

    from indtrader.data.announce import triage_title

    if not store.con.execute("SELECT 1 FROM sqlite_master WHERE name='news_seen'").fetchone():
        return []
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for d, title in store.con.execute(
            "SELECT d, title FROM news_seen WHERE symbol=? AND d>=? ORDER BY d DESC LIMIT ?",
            [sym.upper(), cutoff, cap * 3]).fetchall():
        m = triage_title(title or "")
        label = f"[{m[0]}] " if m else ""
        out.append([str(d), label + str(title)[:110]])
        if len(out) >= cap:
            break
    return out


def green_title(title: str) -> tuple[str, ...] | None:
    """GREEN terms on a headline (word-boundary, case-insensitive) — no RED/AMBER
    filtering here; good_news() is the decision function."""
    t = (title or "").lower()
    hits = tuple(w for w in GREEN if re.search(r"(?<![a-z])" + re.escape(w), t))
    return hits or None


def good_news(store, symbols: list[str], days: int = 3) -> list[dict]:
    """Held-name headlines that read GOOD and are NOT also RED/AMBER-flagged —
    a headline matching both ships as the warning only, never the good-news spin.
    Newest-first dicts {symbol, d, title, terms}. The rows come from the storage
    layer (data.news.good_recent); the judgement lives here."""
    from indtrader.data.announce import triage_title
    from indtrader.data.news import good_recent

    out = []
    for sym, d, title in good_recent(store, symbols, days=days):
        g = green_title(title)
        if not g or triage_title(title):
            continue
        out.append({"symbol": sym, "d": d, "title": title[:200], "terms": g})
    return sorted(out, key=lambda r: r["d"], reverse=True)


def _already_sent(log, text: str) -> bool:
    import hashlib

    h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return log.con.execute(
        "SELECT 1 FROM signals WHERE source='tg-holdings-alert' AND payload LIKE ?",
        [f'%"{h}"%']).fetchone() is not None


def _mark_sent(log, text: str) -> None:
    import hashlib

    log.record_signal("HOLDINGS", "tg-holdings-alert",
                      {"sha1": hashlib.sha1(text.encode("utf-8")).hexdigest(), "text": text[:300]})


def send_holdings_alerts(store, log, settings=None) -> dict:
    """Results countdown + good-news alerts to Telegram, deduped by message text.
    Honest by construction: telegram unconfigured -> (False, 'not configured')
    and NOTHING marked sent (no silent no-op that looks like a quiet day); the
    signals rows land only after send() says ok. Returns a report dict — the
    loop prints it, never fakes quiet."""
    from indtrader.alerts.pause import is_paused
    from indtrader.alerts.telegram import is_configured, send
    from indtrader.config import get_settings
    from indtrader.portfolio.valuation import held_quantities

    s = settings or get_settings()
    held = sorted(s for s in held_quantities(log.fills()) if not str(s).upper().startswith("MF:"))
    res = {"held": len(held), "results_sent": [], "results_unknown": [], "news_sent": [],
           "skipped_pause": False, "sent": False, "reason": ""}
    if not held:
        res["reason"] = "no holdings recorded — nothing to watch"
        return res

    messages = [m["text"] for m in _results_messages(store, held)]
    unknown = _results_unknown(store, held)
    res["results_unknown"] = unknown
    if unknown:
        print(f"[holdings] results date UNKNOWN for: {', '.join(unknown)} — "
              "countdown cannot fire (run: indtrader events)")
    for g in good_news(store, held):
        messages.append(f"Good news on your holding — {g['symbol']} ({g['d']}): {g['title']}")

    if not messages:
        res["reason"] = "nothing due: no held results within " \
                        f"{RESULTS_ALERT_DAYS} days, no unalerted good headlines"
        return res

    if is_paused(s):
        res["skipped_pause"] = True
        res["reason"] = "loop PAUSED — alerts held back (indtrader resume)"
        return res
    if not is_configured(s):
        res["reason"] = "telegram not configured (set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)"
        return res

    ok_any = False
    for text in messages:
        if _already_sent(log, text):
            continue
        sent, why = send("ind-trader holdings\n" + text, s)
        if sent:
            _mark_sent(log, text)
            ok_any = True
            if text.startswith("Good news"):
                res["news_sent"].append(text[:90])
            else:
                res["results_sent"].append(text.splitlines()[0][:90])
        else:
            res["reason"] = f"send failed: {why}"
    res["sent"] = ok_any
    return res


def _results_unknown(store, held: list[str]) -> list[str]:
    from indtrader.research import events

    return [s for s in held if events.next_event(store, s) is None]


def _results_messages(store, held: list[str]) -> list[dict]:
    """One {'text': ...} per held name whose stored results date is
    0..RESULTS_ALERT_DAYS away. Names with NO stored date never appear here —
    unknowns are reported separately by _results_unknown(), printed and listed,
    never faked into a message or silently skipped."""
    from datetime import date

    from indtrader.research import events

    out = []
    today = date.today()
    for s in held:
        d = events.next_event(store, s)
        if not d:
            continue
        days = (date.fromisoformat(str(d)[:10]) - today).days
        if 0 <= days <= RESULTS_ALERT_DAYS:
            when = "TODAY" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
            out.append({"text": f"{s} quarterly results {when} ({d}) — you hold it. "
                                "Re-check your thesis and levels BEFORE the print; "
                                "the plan rule blocks fresh entries within 2 sessions."})
    return out
