"""External NEWS for holdings + market — the feed layer; the JUDGEMENT layer is
reused, not reinvented: headlines pass through the SAME RED/AMBER keyword triage
as exchange announcements (indtrader.data.announce), so "flagged" means the same
thing in both sections and a news word can't invent a new category.

Feed: Google News RSS (plain XML over HTTP; no API key, stdlib urllib+ElementTree,
reachable from networks that 403 NSE). Honest rules, same as everywhere here:
  - empty feed with an HTTP error -> status "unreachable" for that query, never
    a clean-looking zero;
  - rows missing title/link are counted and reported, not dropped silently;
  - triage gates what we SURFACE (store keeps raw rows for history).
Query templates live in one dict — a blocked or reshaped feed is one edit there.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, timedelta
from urllib.parse import quote

from indtrader.data.announce import Match, triage_title
from indtrader.data.http import transport as http_transport
from indtrader.data.http import http_get
from indtrader.data.store import BarStore

NEWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_seen(
    link TEXT PRIMARY KEY,
    symbol TEXT, title TEXT, d TEXT, source TEXT, fetched_on TEXT
);
"""

# query key -> Google News RSS search. {sym} substituted per symbol; MARKET = macro.
QUERY_TPL = 'https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en'
MARKET_QUERY = 'nifty OR "sensex" OR "india stock market" when:2d'
SYM_QUERY = '"{sym}" stock OR shares NSE when:2d'
MAX_SYM_QUERIES = 20  # a nightly fetch stays under ~30 requests even for big books


def fetch_feed(url: str) -> tuple[list[dict], int, str | None]:
    """One feed -> ([{title, link, pubDate}], skipped_count, error|None). All
    three parts matter: 0 items + error None is a genuinely quiet day; 0 + error
    is an OUTAGE, and callers must not read the outage as calm. `skipped` counts
    rows we refused to parse — never dropped silently. Transport lives in
    data.http: round-5 live showed this laptop's WAF rejects plain urllib, and
    curl_cffi's Chrome impersonation is the route confirmed working there."""
    body, err = http_get(url, referer="https://news.google.com/",
                         accept="application/rss+xml, application/xml")
    if err:
        return [], 0, err
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        return [], 0, f"ParseError: {str(e)[:120]}"
    out, skipped = [], 0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            skipped += 1
            continue
        out.append({"title": title, "link": link, "pubDate": item.findtext("pubDate") or ""})
    return out, skipped, None


def store_news(store: BarStore, symbol: str, items: list[dict], source: str) -> int:
    """Idempotent by link — nightly re-runs cost nothing. Returns NEW rows."""
    store.con.executescript(NEWS_SCHEMA)
    today = date.today().isoformat()
    n = 0
    for it in items:
        link = (it.get("link") or "").strip()
        title = (it.get("title") or "").strip()
        if not link or not title:
            continue
        cur = store.con.execute(
            "INSERT OR IGNORE INTO news_seen VALUES (?,?,?,?,?,?)",
            (link[:900], symbol.upper(), title[:300], today, source, today))
        n += cur.rowcount
    store.con.commit()
    return n


def fetch_news(symbols: list[str], store: BarStore) -> dict:
    """Fetch MARKET + per-symbol queries (cap MAX_SYM_QUERIES). Per-query honest
    status; ok=False only when EVERY query failed (that's an outage, not a quiet day)."""
    queries = [("MARKET", MARKET_QUERY)]
    for sym in [s.upper() for s in symbols][:MAX_SYM_QUERIES]:
        queries.append((sym, SYM_QUERY.format(sym=sym)))
    stored, unreachable, failed, skipped = 0, [], [], 0
    for sym, q in queries:
        items, skip, err = fetch_feed(QUERY_TPL.format(q=quote(q)))
        skipped += skip
        if err:
            (unreachable if err.split(":")[0] in ("URLError", "HTTPError", "ParseError",
                                                  "TimeoutError", "OSError")
             else failed).append(f"{sym}: {err}")
            continue
        stored += store_news(store, sym, items, "gnews")
    return {"ok": not (len(unreachable) + len(failed) == len(queries)),
            "transport": http_transport(),
            "stored_new": stored, "queries": len(queries), "skipped_rows": skipped,
            "unreachable": unreachable, "failed": failed}


def triaged_recent(store: BarStore, symbols: list[str], days: int = 7) -> list[Match]:
    """Stored news triaged RED-first, MARKET shown as its own 'symbol'. Empty
    symbols => empty list (holdings-only by design — the macro query rows ride in
    under the explicit 'MARKET' name whenever the caller asks for it by name)."""
    store.con.executescript(NEWS_SCHEMA)
    if not symbols:
        return []
    syms = [s.upper() for s in symbols]
    q = ("SELECT symbol, d, title FROM news_seen WHERE symbol IN ({}) ORDER BY d DESC "
         "LIMIT 500").format(",".join("?" * len(syms)))
    since = (date.today() - timedelta(days=int(days))).isoformat()
    out: list[Match] = []
    for sym, d, title in store.con.execute(q, syms).fetchall():
        m = triage_title(title)
        if m and d >= since:
            out.append(Match(sym, d, title, m[0], m[1]))
    out.sort(key=lambda m: (m.severity != "RED", m.symbol, m.d), reverse=False)
    return out


def good_recent(store: BarStore, symbols: list[str], days: int = 3) -> list[tuple]:
    """Raw (symbol, d, title) rows for these symbols within `days` — NO judgement
    applied here (that's the caller's GREEN/triage job, same split as this
    module's docstring states). Empty symbols or a missing table => [] honestly:
    no rows stored is genuinely nothing stored, not a feed outage (fetch_news
    reports outages at fetch time)."""
    store.con.executescript(NEWS_SCHEMA)
    if not symbols:
        return []
    since = (date.today() - timedelta(days=int(days))).isoformat()
    q = ("SELECT symbol, d, title FROM news_seen WHERE symbol IN ({}) AND d >= ? "
         "ORDER BY d DESC LIMIT 500").format(",".join("?" * len(symbols)))
    return [(str(r[0]).upper(), str(r[1]), str(r[2]))
            for r in store.con.execute(q, [s.upper() for s in symbols] + [since]).fetchall()]


def format_alert(matches: list[Match]) -> str:
    if not matches:
        return "News: nothing keyword-flagged on holdings (7d) — quiet or feed down; check fetch status"
    lines = [f"News triage ({len(matches)} flagged):"]
    for m in matches[:20]:
        lines.append(f" [{m.severity}] {m.symbol} {m.d}: {m.title[:110]}")
    return "\n".join(lines)
