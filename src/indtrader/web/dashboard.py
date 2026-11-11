"""Thin read-only web dashboard (plan §E) — ONE page, stdlib only.

Everything the CLI sees, on one screen: holdings + open plans + regime + the
scan shortlist, PLUS the operator's ledger sections — ETL/Phase-4 discipline,
the split-gap ledger, UNRESOLVED corporate actions, the holiday drift ledger,
flagged news and shareholding alerts — rendered from the same sqlite stores
the CLI reads. No new dependencies (Smart App Control on the office box blocks
new native packages anyway), no authentication (bind 127.0.0.1 only — this is
a personal tool on the operator's laptop; the one sanctioned exception is an
explicit --host inside a container you port-map yourself), and strictly
read-only: the request handler contains no write path, no subprocess, no
order anything. The sparklines/heatmap are server-generated SVG strings from
fixture-testable code — no client JS beyond table building.

Endpoints:
  GET /            -> the page (HTML, auto-refresh 5 min)
  GET /api/status  -> JSON snapshot the page renders from
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="300"><title>ind-trader</title>
<style>
body{font:14px/1.45 system-ui;margin:2rem auto;max-width:1060px;color:#16202b;background:#f7f8fa}
h1{font-size:1.3rem}h2{font-size:1.05rem;margin-top:1.4rem}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border:1px solid #d7dde5;padding:4px 8px;text-align:left;font-variant-numeric:tabular-nums}
th{background:#eef1f5}.bad{color:#b3261e}.ok{color:#1a7f37}.muted{color:#6b7684}.amber{color:#8a5a00}
.banner{padding:6px 10px;border-radius:6px;background:#fff;border:1px solid #d7dde5;margin:6px 0}
td svg{display:block}
</style></head><body>
<h1>ind-trader — read-only</h1><div id="app">loading…</div>
<script>
async function draw(){
  const d = await (await fetch('/api/status')).json();
  const t=(h,rows)=>`<table><tr>${h.map(x=>`<th>${x}</th>`).join('')}</tr>${
    rows.map(r=>`<tr>${r.map(c=>`<td>${c==null?'':c}</td>`).join('')}</tr>`).join('')}</table>`;
  const sec=(cond,title,headers,rows)=>cond?`<h2>${title}</h2>`+t(headers,rows):'';
  let h='';
  h+=`<div class="banner">${d.banner||''}</div>`;
  if(d.health && d.health.error) h+=`<div class="banner bad">data health: ${d.health.error}</div>`;
  else if(d.health) h+=`<div class="banner">health: ${d.health.symbols} symbols · ${d.health.problem} problem (file) · latest bars ${d.health.ok}/${d.health.symbols}</div>`;
  if(d.etl) h+=sec(true,'ETL / Phase-4 discipline',['metric','value'],d.etl);
  if(d.heat && d.heat.days && d.heat.rows.length){
    const cols=d.heat.days.map(x=>`<th>${x}</th>`).join('');
    const body=d.heat.rows.map(r=>`<tr><td>${r[0]}</td>`+d.heat.days.map(day=>{
      const v=r[1][day];
      const cls=v==='fresh'?'ok':'bad';
      const txt=v==null?'':(v==='fresh'?'●':'○');
      return `<td class="${cls}" style="text-align:center">${txt}</td>`;}).join('')+`</tr>`).join('');
    h+=`<h2>Freshness heatmap <span class="muted">(● bar present · ○ no bar on an index session · blank = pre-listing — never a fake miss)</span></h2>
        <table><tr><th>symbol</th>${cols}</tr>${body}</table>`;
  }
  h+=sec((d.split_gaps||[]).length,'Split-gap ledger — same arithmetic as health',['symbol','unexplained cliffs'],d.split_gaps);
  if(d.split_gaps && !d.split_gaps.length) h+=`<h2>Split-gap ledger</h2><div class="banner ok">every big raw move is explained by a split-sized factor step</div>`;
  h+=sec((d.unresolved_ca||[]).length,'Corporate actions — UNRESOLVED (no ratio, never guessed)',['symbol','date','kind'],d.unresolved_ca);
  if(d.holidays){
    h+=`<h2>Holiday drift ledger</h2><div class="banner">master ${d.holidays.coverage||'?'} — `+
       (d.holidays.disputed.length?`<span class="bad">${d.holidays.disputed.join(', ')}</span>`
                                  :`<span class="ok">no drift days</span>`) +
       (d.holidays.master_missing?` <span class="bad">${d.holidays.master_missing}</span>`:'')+`</div>`;
  }
  h+=sec((d.news||[]).length,'News flagged (RED/AMBER triage)',['date','flag','item'],d.news);
  if(d.news_note) h+=`<div class="muted">${d.news_note}</div>`;
  h+=sec((d.shareholding||[]).length,'Shareholding watch',['alert'],d.shareholding);
  h+=sec((d.book||[]).length,'Book',['symbol','qty','mark','value','60d','plan'],d.book);
  h+=sec((d.plans||[]).length,'Open plans',['symbol','entry','stop','target','created','reason'],d.plans);
  h+=sec((d.shortlist||[]).length,'Last scan',['symbol','close','score','60d','note'],d.shortlist);
  if((d.problems||[]).length) h+=`<h2 class="bad">Journal gaps</h2><ul>${d.problems.map(p=>`<li class="bad">${p}</li>`).join('')}</ul>`;
  document.getElementById('app').innerHTML=h||'nothing yet — run scan/fill';
}
draw().catch(e=>document.getElementById('app').textContent='error: '+e);
</script></body></html>"""


def sparkline(values, width: int = 110, height: int = 26) -> str:
    """60-day close as a server-side SVG polyline — Option C in the mockups, no
    client JS, no packages. Fewer than 2 points or a flat series yields a muted
    dash: not enough shape to plot, and a flat line pretending otherwise is the
    dead-feed-as-quiet failure this repo refuses."""
    try:
        vs = [float(v) for v in values if v is not None]
    except (TypeError, ValueError):
        vs = []
    if len(vs) < 2 or max(vs) == min(vs):
        return (f'<svg width="{width}" height="{height}"><line x1="0" y1="{height // 2}" '
                f'x2="{width}" y2="{height // 2}" stroke="#b9c2cc" stroke-dasharray="3 3"/></svg>')
    lo, hi = min(vs), max(vs)
    n = len(vs)
    pts = " ".join(f"{i * width / (n - 1):.1f},{height - 2 - (v - lo) / (hi - lo) * (height - 4):.1f}"
                   for i, v in enumerate(vs))
    color = "#1a7f37" if vs[-1] >= vs[0] else "#b3261e"
    return (f'<svg width="{width}" height="{height}"><polyline fill="none" stroke="{color}" '
            f'stroke-width="1.5" points="{pts}"/></svg>')


def snapshot(store, log, universe: list[str]) -> dict:
    """Compose the page's data from existing read APIs. Missing data yields labeled
    empties, never invented numbers (the health banner is the honest zero-check)."""
    import pandas as pd

    from indtrader.alerts.pause import is_paused
    from indtrader.portfolio.valuation import held_quantities, resolve_marks
    from indtrader.research.calendar import staleness_days, index_sessions
    from indtrader.research.splitgaps import SPLIT_JUMP, symbol_split_gaps

    out: dict = {"banner": "", "book": [], "plans": [], "shortlist": [],
                 "problems": [], "health": None, "regime": None,
                 "split_gaps": [], "unresolved_ca": [], "holidays": None,
                 "news": [], "news_note": "", "shareholding": [], "heat": None,
                 "etl": []}
    idx = store.load("NIFTY")
    if idx.empty:
        out["banner"] = "no NIFTY bars — ETL gap; nothing below is trustworthy"
    else:
        import numpy as np

        last = float(idx["close"].iloc[-1])
        wk = idx["close"].tail(6)
        chg = float(wk.iloc[-1] / wk.iloc[0] - 1) if len(wk) > 1 else 0.0
        age = int(np.busday_count(pd.Timestamp(idx.index.max()).date(),
                                  pd.Timestamp.today().date()))
        out["banner"] = (f"NIFTY {last:,.0f} ({chg:+.2%} 5d) at {idx.index[-1].date()}"
                         + (f" · {age} business days STALE" if age > 5 else "")
                         + (" · PAUSED" if is_paused() else ""))

    fills = log.fills()
    held = held_quantities(log.fills())
    marks = resolve_marks(store, held)
    plans = {r[0]: r for r in log.con.execute(
        "SELECT symbol,entry,stop,target,created,reason FROM plans "
        "WHERE status='executed' ORDER BY created DESC").fetchall()}
    used = set()
    for sym, q in sorted(held.items()):
        p = plans.get(sym) or plans.get(sym.replace("MF:", ""))
        if p:
            used.add(p[0])
        m = marks.get(sym)
        df_b = store.load(sym)
        out["book"].append([sym, round(q, 4), None if m is None else round(m, 2),
                            None if m is None else round(q * m, 0),
                            sparkline(df_b.tail(60)["close"]),
                            "" if not p else f"entry {p[1]} stop {p[2]} tgt {p[3]}"])
    for r in log.con.execute("SELECT symbol,entry,stop,target,created,reason FROM plans "
                             "WHERE status='open' ORDER BY created").fetchall():
        out["plans"].append([r[0], r[1], r[2], r[3], str(r[4])[:16], str(r[5] or "")[:60]])
    row = log.con.execute(
        "SELECT payload FROM signals WHERE source='scan' ORDER BY rowid DESC LIMIT 1").fetchone()
    if row:  # scan journals one UNIVERSE row whose payload carries the picks
        try:
            p = json.loads(row[0])
        except (ValueError, TypeError):
            p = {}
        out["regime"] = {"label": p.get("regime"), "exposure_scale": p.get("exposure_scale")}
        for pick in (p.get("picks") or [])[:10]:
            closes = store.load(pick.get("symbol") or "").tail(60)["close"]
            out["shortlist"].append([pick.get("symbol"), pick.get("close") or pick.get("quadrant"),
                                     pick.get("score"), sparkline(closes),
                                     str(pick.get("note") or "")[:60]])

    # journal gaps: executed-planless fills among EQUITY rows (CA synthetics/SIPs labeled elsewhere)
    executed = {s for (s,) in log.con.execute(
        "SELECT DISTINCT symbol FROM plans WHERE status IN ('executed')").fetchall()}
    for sym in {f.symbol.upper() for f in fills if not f.symbol.upper().startswith("MF:")}:
        if sym not in executed and sym not in used:
            out["problems"].append(f"{sym}: fills without an executed plan")

    if idx is not None and not idx.empty and universe:
        from datetime import date

        problems = 0
        # plain datetime.date everywhere: Timestamps refuse to hash/compare with date
        bar_days = {ts.date() for ts in pd.DatetimeIndex(pd.to_datetime(idx.index)).normalize()}
        days = [d.date() for d in pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=10)]
        heat: dict[str, dict[str, str]] = {}
        latest_ok = 0
        for sym in universe:
            df = store.load(sym)
            stale = (df.empty or staleness_days(df.index, index_sessions(idx)) is None
                     or (staleness_days(df.index, index_sessions(idx)) or 0) > 3)
            if stale:
                problems += 1
            else:
                latest_ok += 1
            per: dict[str, str] = {}
            if not df.empty:
                sym_days = {ts.date() for ts in pd.DatetimeIndex(pd.to_datetime(df.index)).normalize()}
                start = min(sym_days)
                for day in days:
                    if day in bar_days and day >= start:  # pre-listing days: no cell, never a fake miss
                        per[str(day)] = "fresh" if day in sym_days else "missing"
                gaps = symbol_split_gaps(store, sym, df, SPLIT_JUMP)
                if gaps:
                    out["split_gaps"].append([sym, "; ".join(gaps[:3]) + (" …" if len(gaps) > 3 else "")])
            heat[sym] = per
        out["heat"] = {"days": [str(d) for d in days],
                       "rows": [[s, heat.get(s, {})] for s in universe]}
        out["health"] = {"symbols": len(universe), "problem": problems, "ok": latest_ok,
                         "last_bar": str(idx.index.max().date())}
        out["etl"] = _etl_section(store, idx, latest_ok, len(universe))

        # holiday drift ledger — offline re-computable, names days or says "no drift"
        from indtrader.data import macrocal

        cov = macrocal.calendar_status(store)
        out["holidays"] = {
            "coverage": f"{cov[0]}..{cov[1]}" if cov else None,
            "disputed": macrocal.disputed_dates(store),
            "master_missing": None if cov else "holiday master not fetched — drift unknown, not zero"}

        # corporate actions stored WITHOUT a usable ratio (split-like kinds only —
        # a dividend with no ratio is complete, not unresolved)
        from indtrader.research.coractions import is_split_kind

        for sym, d, kind in store.con.execute(
                "SELECT symbol, d, kind FROM corporate_actions WHERE ratio IS NULL "
                "ORDER BY symbol, d").fetchall():
            if is_split_kind(kind or ""):
                out["unresolved_ca"].append([sym, str(d)[:10], str(kind)[:60]])

        # shareholding alerts for held names (empty table -> nothing to alert on)
        from indtrader.research import shareholding as sh

        out["shareholding"] = sh.holding_alerts(store, [s for s in held if not s.startswith("MF:")])
    else:
        out["health"] = {"error": "no NIFTY calendar or universe file"}

    # flagged news: stored rows through the SAME triage as announcements
    out["news"], out["news_note"] = _flagged_news(store)
    return out


def _etl_section(store, idx, latest_ok: int, n_universe: int) -> list:
    """Phase-4 view: the loop's own output is stdout, not the DB — so this section
    shows what the DB proves (freshness, last bar) and NAMES the missing evidence
    (a run-log table) instead of fabricating a clean-days streak counter."""
    import pandas as pd

    last = idx.index.max()
    rows = [["latest index bar", str(last.date())],
            ["symbols with bars", str(store.con.execute(
                "SELECT count(DISTINCT symbol) FROM candles WHERE tf='daily'").fetchone()[0])],
            ["fresh now (≤3 sessions)", f"{latest_ok}/{n_universe}"]]
    has_runs = store.con.execute(
        "SELECT 1 FROM sqlite_master WHERE name='etl_runs'").fetchone()
    rows.append(["clean-day streak", "not persisted — count [etl-loop] lines in the loop log"
                 if not has_runs else "see etl_runs table"])
    return rows


def _flagged_news(store, days: int = 3, cap: int = 30) -> tuple[list, str]:
    """Stored news through the SAME RED/AMBER triage as exchange announcements.
    No table at all = the loop never fetched; a table with zero RED/AMBER rows =
    genuinely quiet. Saying one when it's the other is the dead-feed-as-quiet lie."""
    from indtrader.data.announce import triage_title

    if not store.con.execute("SELECT 1 FROM sqlite_master WHERE name='news_seen'").fetchone():
        return [], "news not fetched yet — runs with the evening loop (fetch-feeds/news)"
    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = store.con.execute(
        "SELECT d, symbol, title FROM news_seen WHERE d>=? ORDER BY d DESC LIMIT 400",
        [cutoff]).fetchall()
    out = []
    for d, sym, title in rows:
        m = triage_title(title or "")
        if m:
            sev, _terms = m
            out.append([d, sev, (f"{sym}: " if sym else "") + str(title)[:110]])
            if len(out) >= cap:
                break
    return out, (f"{len(rows)} items stored ≤{days}d; {len(out)} flagged" if not out
                 else "")


def make_handler(store_factory, log_factory, universe):
    """Takes zero-arg factories, not connections: a request may be served on any
    thread and sqlite connections are thread-bound, so each request opens its own
    and closes it on the way out. Read-only throughout — no write path exists here."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib API)
            if self.path == "/":
                body, ctype = PAGE, "text/html; charset=utf-8"
            elif self.path == "/api/status":
                store, log = store_factory(), log_factory()
                try:
                    body = json.dumps(snapshot(store, log, universe), default=str)
                except Exception as e:  # report inside the envelope, page keeps working
                    body = json.dumps({"banner": f"snapshot error: {type(e).__name__}: {e}"})
                finally:
                    store.close()
                    log.close()
                ctype = "application/json"
            else:
                self.send_error(404)
                return
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):  # quiet by default
            pass
    return H


def serve(host: str = "127.0.0.1", port: int = 8765) -> int:
    from http.server import ThreadingHTTPServer

    from indtrader.data.store import BarStore
    from indtrader.data.universe import load_universe
    from indtrader.execution.planlog import PlanLog

    if host not in ("127.0.0.1", "localhost"):
        # no auth, so LAN exposure needs an explicit flag — the sanctioned case is
        # the Docker service (--host 0.0.0.0 + a port map the operator controls).
        print(f"WARNING: dashboard binding {host} — no auth; only do this where "
              "you control who can reach the port (e.g. Docker port map)")
    universe = load_universe(None)
    # Per-request connections make threading moot; auto-refresh polls every 5 min.
    srv = ThreadingHTTPServer((host, port), make_handler(BarStore, PlanLog, universe))
    print(f"ind-trader dashboard (read-only): http://{host}:{port}  — Ctrl-C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()  # connections are per-request — nothing else to close here
    return 0
