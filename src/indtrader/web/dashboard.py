"""Thin read-only web dashboard (plan §E) — ONE page, stdlib only.

Holdings + open plans + regime + data health + the scan shortlist, rendered from
the same sqlite stores the CLI uses. No new dependencies (Smart App Control on the
office box blocks new native packages anyway), no authentication (bind 127.0.0.1
only — this is a personal tool on the operator's laptop), and strictly read-only:
the request handler contains no write path, no subprocess, no order anything.

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
body{font:14px/1.45 system-ui;margin:2rem auto;max-width:1000px;color:#16202b;background:#f7f8fa}
h1{font-size:1.3rem}h2{font-size:1.05rem;margin-top:1.4rem}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border:1px solid #d7dde5;padding:4px 8px;text-align:left;font-variant-numeric:tabular-nums}
th{background:#eef1f5}.bad{color:#b3261e}.ok{color:#1a7f37}.muted{color:#6b7684}
.banner{padding:6px 10px;border-radius:6px;background:#fff;border:1px solid #d7dde5;margin:6px 0}
</style></head><body>
<h1>ind-trader — read-only</h1><div id="app">loading…</div>
<script>
async function draw(){
  const d = await (await fetch('/api/status')).json();
  const t=(h,rows)=>`<table><tr>${h.map(x=>`<th>${x}</th>`).join('')}</tr>${
    rows.map(r=>`<tr>${r.map(c=>`<td>${c==null?'':c}</td>`).join('')}</tr>`).join('')}</table>`;
  let h='';
  h+=`<div class="banner">${d.banner||''}</div>`;
  if(d.health && d.health.error) h+=`<div class="banner bad">data health: ${d.health.error}</div>`;
  else if(d.health) h+=`<div class="banner">health: ${d.health.symbols} symbols · ${d.health.problem} problem (file)</div>`;
  if(d.book && d.book.length) h+=`<h2>Book</h2>`+t(['symbol','qty','mark','value','plan'],d.book);
  if(d.plans && d.plans.length) h+=`<h2>Open plans</h2>`+t(['symbol','entry','stop','target','created','reason'],d.plans);
  if(d.shortlist && d.shortlist.length) h+=`<h2>Last scan</h2>`+t(['symbol','close','score','note'],d.shortlist);
  if((d.problems||[]).length) h+=`<h2 class="bad">Journal gaps</h2><ul>${d.problems.map(p=>`<li class="bad">${p}</li>`).join('')}</ul>`;
  document.getElementById('app').innerHTML=h||'nothing yet — run scan/fill';
}
draw().catch(e=>document.getElementById('app').textContent='error: '+e);
</script></body></html>"""


def snapshot(store, log, universe: list[str]) -> dict:
    """Compose the page's data from existing read APIs. Missing data yields labeled
    empties, never invented numbers (the health banner is the honest zero-check)."""
    import pandas as pd

    from indtrader.alerts.pause import is_paused
    from indtrader.portfolio.valuation import held_quantities, resolve_marks
    from indtrader.research.calendar import staleness_days, index_sessions

    out: dict = {"banner": "", "book": [], "plans": [], "shortlist": [],
                 "problems": [], "health": None, "regime": None}
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
    held = held_quantities(fills)
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
        out["book"].append([sym, round(q, 4), None if m is None else round(m, 2),
                            None if m is None else round(q * m, 0),
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
            out["shortlist"].append([pick.get("symbol"), pick.get("close") or pick.get("quadrant"),
                                     pick.get("score"), str(pick.get("note") or "")[:60]])

    # journal gaps: executed-planless fills among EQUITY rows (CA synthetics/SIPs labeled elsewhere)
    executed = {s for (s,) in log.con.execute(
        "SELECT DISTINCT symbol FROM plans WHERE status IN ('executed')").fetchall()}
    for sym in {f.symbol.upper() for f in fills if not f.symbol.upper().startswith("MF:")}:
        if sym not in executed and sym not in used:
            out["problems"].append(f"{sym}: fills without an executed plan")

    if idx is not None and not idx.empty and universe:
        problems = 0
        for sym in universe:
            df = store.load(sym)
            if df.empty or staleness_days(df.index, index_sessions(idx)) is None or \
               (staleness_days(df.index, index_sessions(idx)) or 0) > 3:
                problems += 1
        out["health"] = {"symbols": len(universe), "problem": problems}
    else:
        out["health"] = {"error": "no NIFTY calendar or universe file"}
    return out


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
        print("dashboard binds loopback only (personal tool, no auth) — using 127.0.0.1")
        host = "127.0.0.1"
    universe = load_universe(None)
    # Per-request connections make threading moot; auto-refresh polls every 5 min.
    srv = ThreadingHTTPServer((host, port), make_handler(BarStore, PlanLog, universe))
    print(f"ind-trader dashboard (read-only): http://{host}:{port}  — Ctrl-C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        store.close()
        log.close()
    return 0
