"""Thin read-only web dashboard (plan §E) — ONE page, stdlib only.

The page answers the only question the operator asks it, first: "what do I do
today?" — a plain-language verdict plus one card per open decision, each card
naming the follow-up command. Everything technical (ETL ledger, freshness
heatmap, split-gap ledger, UNRESOLVED corporate actions, holiday drift, raw
tables, sparklines) lives in collapsed <details> dropdowns below: same data,
same honesty, one click away. The plain words are built SERVER-side in Python
(fixture-testable), never in client JS.

Read-only is structural, not a slogan: holdings + open plans + regime + the
scan shortlist + the operator ledgers, all rendered from the same sqlite stores
the CLI reads. No new dependencies (Smart App Control on the office box blocks
new native packages anyway), no authentication (bind 127.0.0.1 only — this is
a personal tool on the operator's laptop; the one sanctioned exception is an
explicit --host inside a container you port-map yourself), and the request
handler contains no write path, no subprocess, no order anything. The
sparklines/heatmap are server-generated SVG strings from fixture-testable
code — no client JS beyond table building.

Every card states the no-order fact in words, not by omission: the system
never places orders; a human clicks at the broker, then records it here.

Endpoints:
  GET /            -> the page (HTML, auto-refresh 5 min)
  GET /api/status  -> JSON snapshot the page renders from
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="300"><title>ind-trader — today</title>
<style>
body{font:15px/1.5 system-ui;margin:1.6rem auto;max-width:1060px;color:#16202b;background:#f7f8fa}
h1{font-size:1.25rem;margin:0 0 .2rem}h2{font-size:1.05rem;margin:1.4rem 0 .4rem}
table{border-collapse:collapse;width:100%;background:#fff;margin:.4rem 0}
th,td{border:1px solid #d7dde5;padding:4px 8px;text-align:left;font-variant-numeric:tabular-nums}
th{background:#eef1f5}.bad{color:#b3261e}.ok{color:#1a7f37}.muted{color:#6b7684}.amber{color:#8a5a00}
.banner{padding:6px 10px;border-radius:6px;background:#fff;border:1px solid #d7dde5;margin:6px 0}
td svg{display:block}
.verdict{padding:14px 16px;border-radius:10px;border:2px solid #d7dde5;background:#fff;font-size:1.05rem;margin:10px 0}
.verdict.green{border-color:#1a7f37;background:#f2faf4}
.verdict.amber{border-color:#8a5a00;background:#fdf8ec}
.verdict.red{border-color:#b3261e;background:#fdf1f0}
.card{padding:12px 14px;border:1px solid #d7dde5;border-left-width:4px;border-radius:8px;background:#fff;margin:8px 0}
.card.amber{border-left-color:#8a5a00}.card.red{border-left-color:#b3261e}.card.green{border-left-color:#1a7f37}
.card b{font-size:1rem}
.card .after{display:block;margin-top:6px;padding:5px 8px;background:#eef1f5;border-radius:5px;font-family:ui-monospace,monospace;font-size:12.5px}
details{background:#fff;border:1px solid #d7dde5;border-radius:8px;padding:8px 14px;margin:10px 0}
summary{cursor:pointer;font-weight:600;font-size:1rem}
summary .muted{font-weight:400}
details.inrow{border:none;background:transparent;padding:0;margin:0}
details.inrow summary{font-weight:400;font-size:13px;color:#3b5bdb}
details.inrow ul{margin:4px 0 4px 18px;padding:0}
details.inrow li{margin:2px 0}
.legend{font-size:13px}
</style></head><body>
<h1>ind-trader — what to do today</h1>
<div class="muted">read-only · this page never trades — you decide, you click at your broker, you record it here</div>
<div id="app">loading…</div>
<script>
async function draw(){
  const d = await (await fetch('/api/status')).json();
  const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const t=(h,rows)=>`<table><tr>${h.map(x=>`<th>${esc(x)}</th>`).join('')}</tr>${
    rows.map(r=>`<tr>${r.map(c=>`<td>${c&&c.svg?c.svg:esc(c)}</td>`).join('')}</tr>`).join('')}</table>`;
  const box=(cls,title,body)=>`<div class="card ${cls}"><b>${title}</b><div>${body}</div></div>`;
  let h='';
  if(d.verdict) h+=`<div class="verdict ${esc(d.verdict.level)}">${esc(d.verdict.text)}</div>`;
  h+=`<div class="banner">${esc(d.banner||'')}</div>`;
  if(d.health && d.health.error) h+=`<div class="banner bad">data health: ${esc(d.health.error)}</div>`;
  else if(d.health) h+=`<div class="banner">${d.health.symbols} symbols · ${d.health.problem} with stale bars · latest index session ${esc(d.health.last_bar)} — details in "Data problems" below</div>`;
  h+='<h2>Your moves today</h2>';
  h+=(d.moves||[]).map(m=>box(m.state,m.title,esc(m.body)+(m.after?`<span class="after">next: ${esc(m.after)}</span>`:''))).join('')||'<div class="muted">nothing to show — run scan/fill first</div>';
  h+=`<div class="muted">${esc(d.rule||'')}</div>`;
  const det=(title,legend,inner)=>`<details><summary>${esc(title)} <span class="muted legend">${esc(legend||'')}</span></summary>${inner}</details>`;
  if(d.etl && d.etl.length){
    h+=det('Data pipeline — the last 10 market days','what the database can prove',
      `<h2>ETL / Phase-4 discipline</h2>`+t(['metric','value'],d.etl)+
      (d.heat && d.heat.days && d.heat.rows.length ? (()=>{
        const cols=d.heat.days.map(x=>`<th>${esc(x)}</th>`).join('');
        const body=d.heat.rows.map(r=>`<tr><td>${esc(r[0])}</td>`+d.heat.days.map(day=>{
          const v=r[1][day];
          const cls=v==='fresh'?'ok':'bad';
          const txt=v==null?'':(v==='fresh'?'●':'○');
          return `<td class="${cls}" style="text-align:center">${txt}</td>`;}).join('')+`</tr>`).join('');
        return `<h2>Freshness heatmap <span class="muted">(● bar present · ○ no bar on an index session · blank = pre-listing — never a fake miss)</span></h2>
                <table><tr><th>symbol</th>${cols}</tr>${body}</table>`;})():''));
  }
  {
    let inner='';
    if(d.split_gaps && d.split_gaps.length)
      inner+='<h2>Split-gap ledger — same arithmetic as health</h2>'+t(['symbol','unexplained cliffs'],d.split_gaps);
    else if(d.health && !d.health.error)
      inner+='<h2>Split-gap ledger</h2><div class="banner ok">every big raw move is explained by a split-sized factor step</div>';
    if((d.unresolved_ca||[]).length)
      inner+='<h2>Corporate actions — UNRESOLVED (no ratio, never guessed)</h2>'+t(['symbol','date','kind'],d.unresolved_ca);
    if(d.holidays)
      inner+='<h2>Holiday drift ledger</h2><div class="banner">master '+esc(d.holidays.coverage||'?')+' — '+
        (d.holidays.disputed.length?`<span class="bad">${esc(d.holidays.disputed.join(', '))}</span>`
                                   :'<span class="ok">no drift days</span>')+
        (d.holidays.master_missing?` <span class="bad">${esc(d.holidays.master_missing)}</span>`:'')+'</div>';
    h+=det('Data problems — fix these before trusting any backtest','split gaps · unresolved actions · holiday drift',inner);
  }
  if((d.news||[]).length)
    h+=det(`Warning signs — ${(d.news||[]).length} flagged item(s)`,'news RED/AMBER triage · shareholding',
      '<h2>News flagged (RED/AMBER triage)</h2>'+t(['date','flag','item'],d.news)+
      ((d.shareholding||[]).length?'<h2>Shareholding watch</h2>'+t(['alert'],d.shareholding):''));
  else
    h+=det('Warning signs','none flagged right now',
      (d.shareholding||[]).length?'<h2>Shareholding watch</h2>'+t(['alert'],d.shareholding)
        :'<div class="muted">'+esc(d.news_note||'nothing flagged')+'</div>');
  h+=det('Holdings & open plans — every row','the raw tables behind the cards above',
    t(['symbol','qty','mark','value','60d','plan'],d.book||[])+
    '<h2>Open plans</h2>'+t(['symbol','entry','stop','target','created','reason'],d.plans||[])+
    ((d.problems||[]).length?'<h2 class="bad">Journal gaps</h2><ul>'+d.problems.map(p=>`<li class="bad">${esc(p)}</li>`).join('')+'</ul>':''));
  h+=det("Tonight's shortlist — the last scan's evidence",'ranked picks with 60-day shape — open "why" for the arithmetic',
    (d.shortlist||[]).length?`<table><tr><th>symbol</th><th>score</th><th>quadrant</th><th>60d</th><th>note</th><th></th></tr>${
      d.shortlist.map(r=>{
        const sp=typeof r[3]==='object'&&r[3]&&r[3].svg!==undefined?r[3].svg:'';
        const why=r[5]||{};
        const exp=`<div class="muted">${esc(why.legend||'')}</div><ul>${(why.lines||[]).map(l=>`<li>${esc(l)}</li>`).join('')}</ul>`+
          (why.tail?`<div class="muted">${esc(why.tail)}</div>`:'');
        return `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td>`+
          `<td>${sp}</td><td>${esc(r[4])}</td>`+
          `<td style="white-space:nowrap"><details class="inrow"><summary>why</summary>${exp}</details></td></tr>`;
      }).join('')}</table>`:'<div class="muted">no scan signal stored yet — run the scan this evening</div>');
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
                 "etl": [], "moves": [], "verdict": None, "rule": RULE_TEXT}
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
                            {"svg": sparkline(df_b.tail(60)["close"])},
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
            score = pick.get("score")
            score = None if score is None else round(float(score), 2)
            out["shortlist"].append([pick.get("symbol"), score, pick.get("quadrant") or "",
                                     {"svg": sparkline(closes)},
                                     str(pick.get("note") or "")[:60],
                                     _shortlist_why(pick, score)])

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
        latest_session = max(bar_days)
        latest_missing = []
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
                if latest_session in bar_days and latest_session >= start \
                        and latest_session not in sym_days:
                    latest_missing.append(sym)
                gaps = symbol_split_gaps(store, sym, df, SPLIT_JUMP)
                if gaps:
                    out["split_gaps"].append([sym, "; ".join(gaps[:3]) + (" …" if len(gaps) > 3 else "")])
            heat[sym] = per
        out["heat"] = {"days": [str(d) for d in days],
                       "rows": [[s, heat.get(s, {})] for s in universe]}
        out["health"] = {"symbols": len(universe), "problem": problems, "ok": latest_ok,
                         "last_bar": str(idx.index.max().date()),
                         "latest_missing": latest_missing}
        out["etl"] = _etl_section(store, idx, latest_ok, len(universe))

        # holiday drift ledger — offline re-computable, names days or says "no drift"
        from indtrader.data import macrocal

        cov = macrocal.calendar_status(store)
        out["holidays"] = {
            "coverage": f"{cov[0]}..{cov[1]}" if cov else None,
            "disputed": macrocal.disputed_dates(store),
            "master_missing": None if cov else "holiday master not fetched — drift unknown, not zero"}

        # corporate actions stored WITHOUT a usable ratio (split-like kinds only —
        # a dividend with no ratio is complete, not unresolved). Dates come back
        # WHOLE: they were stored as the exchange wrote them ("07-Sep-2017"), and
        # truncating one to 10 chars mangled it into "07-Sep-201" (laptop screenshot
        # 2026-11-12). The human needs the exact string to build the --set command.
        from indtrader.research.coractions import is_split_kind

        for sym, d, kind in store.con.execute(
                "SELECT symbol, d, kind FROM corporate_actions WHERE ratio IS NULL "
                "ORDER BY symbol, d").fetchall():
            if is_split_kind(kind or ""):
                out["unresolved_ca"].append([sym, str(d), str(kind)[:60]])

        # shareholding alerts for held names (empty table -> nothing to alert on)
        from indtrader.research import shareholding as sh

        out["shareholding"] = sh.holding_alerts(store, [s for s in held if not s.startswith("MF:")])
    else:
        out["health"] = {"error": "no NIFTY calendar or universe file"}

    # flagged news: stored rows through the SAME triage as announcements
    out["news"], out["news_note"] = _flagged_news(store)

    # the plain-words layer — computed here, in Python, so tests can pin the words
    out["verdict"] = _verdict(store, idx, out, fills, held)
    out["moves"] = _moves(store, log, idx, out, held)
    return out


RULE_TEXT = ("Standing rule: this system never places orders — delivery/CNC long-term only, "
             "no intraday, no F&O, no leverage. Every card above is information; the click "
             "at your broker is always a human decision.")

# points research/scanner.py's scan() adds for each RRG quadrant — copied HERE (the
# page explains the stored score with the same arithmetic the scanner used; if the
# scanner's table ever changes, this table must change with it — test_pinned guards)
QUADRANT_POINTS = {"Improving": 2.0, "Leading": 1.5, "Weakening": 0.0, "Lagging": -1.0, "NA": 0.0}
QUADRANT_WORDS = {"Improving": "gaining strength faster than NIFTY lately",
                  "Leading": "already ahead of NIFTY",
                  "Weakening": "losing strength vs NIFTY",
                  "Lagging": "behind NIFTY",
                  "NA": "could not be measured vs NIFTY"}


def _shortlist_why(pick: dict, score) -> dict:
    """The per-row 'why' expander: decompose the stored score with the scanner's
    OWN arithmetic (quadrant points + 6-month momentum rank + bulk boost). The
    momentum-rank part is solved as score minus the two fixed parts — that is
    the same arithmetic, not a guess. Values the payload never carried render
    as 'not stored', never as an invented zero."""
    lines: list[str] = []
    q = str(pick.get("quadrant") or "")
    if q:
        pts = QUADRANT_POINTS.get(q)
        lines.append(f"Relative strength vs NIFTY: {q} — {QUADRANT_WORDS.get(q, 'direction unclear')}"
                     + (f" → {pts:+.1f} points" if pts is not None else ""))
    else:
        lines.append("Relative-strength quadrant: not stored — nothing counted for it")
    m = pick.get("mom_6m")
    if m is not None:
        lines.append(f"6-month return: {float(m):+.1%} — ranked against every other name "
                     "in tonight's run (stronger ranks higher)")
    else:
        lines.append("6-month return: not stored — nothing counted for it")
    nb = pick.get("net_bulk")
    if nb is not None:
        v = float(nb)
        lines.append(f"Institutional bulk deals, net: {'buying' if v > 0 else 'selling' if v < 0 else 'flat'}"
                     f" ₹{abs(v):,.0f} — {'+0.5 added' if v > 0 else 'no addition (only net buying adds)'}")
    else:
        lines.append("Bulk-deal feed: unknown for this name — nothing added, nothing subtracted")
    if score is not None and q in QUADRANT_POINTS and m is not None:
        rank_part = float(score) - QUADRANT_POINTS[q] - (0.5 if (float(nb or 0) > 0) else 0.0)
        lines.append(f"= {QUADRANT_POINTS[q]:+.1f} quadrant + {rank_part:.2f} momentum rank"
                     + (" + 0.50 bulk buying" if float(nb or 0) > 0 else "")
                     + f" → score {float(score):.2f}")
    tail = "Extra eyes — these change nothing about the score: "
    facts, quiet = [], []
    dp, da = pick.get("deliv_per"), pick.get("deliv_avg20")
    if dp is not None:
        facts.append(f"delivery on the latest session {float(dp):.0f}%"
                     + (f" vs 20-day average {float(da):.0f}%" if da is not None else " (average unknown)"))
    else:
        quiet.append("delivery feed unknown")
    if pick.get("turnover_20d") is not None:
        facts.append(f"≈₹{float(pick['turnover_20d']) / 1e7:.1f} crore/day turnover "
                     "(20-session average — is it actually tradable)")
    else:
        quiet.append("turnover unknown")
    bq = pick.get("base_quality")
    facts.append("price base is tight — a coiled range" if bq == "tight"
                 else "price base is wide" if bq == "wide"
                 else "base shape unknown")
    n = str(pick.get("note") or "")
    if n:
        facts.append(f"the scanner's own warning: \"{n}\"")
    return {"headline": f"Why this name is on the list — score {score} explained:",
            "legend": "the score's arithmetic, as the scanner computes it:",
            "lines": lines,
            "tail": tail + "; ".join(facts) + (f"; {', '.join(quiet)}" if quiet else "") + "."}


def _move(state: str, title: str, body: str, after: str = "") -> dict:
    return {"state": state, "title": title, "body": body, "after": after}


def _plan_age_days(created) -> int:
    from datetime import date, datetime

    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created[:19])
        except ValueError:
            return 0
    if isinstance(created, datetime):
        return (date.today() - created.date()).days
    return 0


def _verdict(store, idx, out: dict, fills, held) -> dict:
    """ONE traffic-light sentence, decided in this order: is the data trustworthy
    -> is the market light ON -> is the journal clean. Data truth outranks
    everything: advice computed on old bars is not advice."""
    import pandas as pd

    if idx is None or idx.empty:
        return {"level": "red",
                "text": "STOP — the price database is empty, nothing on this page can be trusted. "
                        "Run the evening data update on the laptop first, then refresh this page."}
    import numpy as np

    age = int(np.busday_count(pd.Timestamp(idx.index.max()).date(), pd.Timestamp.today().date()))
    if age > 5:
        return {"level": "red",
                "text": f"STOP — NIFTY's newest bar is {idx.index[-1].date()}, {age} business days "
                        f"ago. Numbers on stale prices can fake a trend: fix the data pipeline "
                        f"(see 'Data pipeline' below) before acting on anything."}
    dead = (out["health"] or {}).get("latest_missing") or []
    if dead:
        shown = ", ".join(dead[:5]) + (f" (+{len(dead) - 5} more)" if len(dead) > 5 else "")
        return {"level": "red",
                "text": f"STOP — the newest market session has no price bar for {shown}. Until the "
                        f"evening fetch runs, treat everything below as not yet measured."}
    reg = out.get("regime")
    if not reg or not reg.get("label"):
        return {"level": "amber",
                "text": "CAUTION — the market-trend light has not been computed yet: run the scan "
                        "this evening and the light will appear here."}
    label = str(reg["label"]).upper()
    if label in ("OFF", "RISK-OFF"):
        return {"level": "amber",
                "text": "The market-trend light is OFF: this system would hold NO new positions now. "
                        "Existing holdings stay your decision — nothing here forces a sale."}
    scale = reg.get("exposure_scale")
    cap = f" up to {float(scale):.0%} of your money" if scale is not None else ""
    tail = (f"({out['health']['problem']} symbols have stale bars — see 'Data problems' below)"
            if (out["health"] or {}).get("problem") else "data looks current")
    return {"level": "green",
            "text": f"Market-trend light is {label}: it would allow new buying{cap} — "
                    f"every decision below is still yours. {tail}."}


def _moves(store, log, idx, out: dict, held) -> list[dict]:
    """The card list: open plans first (that's where a decision waits), then the
    journal gaps, split gaps and UNRESOLVED actions that block backtests, then
    warnings, then honest 'nothing to do'. Wording rule: facts + the exact next
    command — never 'buy' or 'sell' as advice."""
    M: list[dict] = []
    rows = log.con.execute(
        "SELECT id,symbol,action,qty,entry,stop,target,created,reason FROM plans "
        "WHERE status='open' ORDER BY created").fetchall()
    if rows:
        for pid, sym, action, qty, entry, stop, target, created, reason in rows:
            age = _plan_age_days(created)
            df = store.load(sym)
            bits = [f"Plan #{pid}: you wrote down — {str(action).upper()} {qty:g} {sym} "
                    f"around ₹{entry}, stop ₹{stop}, target ₹{target}. Reason: {str(reason or '')[:80]}"]
            state = "amber"
            if df is None or df.empty:
                bits.append(f"No price history for {sym} in the store — that is UNKNOWN, not zero. "
                            "Fetch it before you decide.")
                state = "red"
            else:
                last = float(df["close"].iloc[-1])
                gap = last / entry - 1.0 if entry else 0.0
                where = ("sits ABOVE its exit level — if you hold it, you may book at the broker "
                         "and record the sale" if last >= target else
                         "sits BELOW its stop — re-examine the reason before any action")
                bits.append(f"Newest close ₹{last:,.2f} ({_pct(gap)} vs your planned entry), so the plan "
                            f"{where}. Still want it? Place a delivery/CNC order at your broker "
                            f"yourself; changed your mind? Cancel — either way the click is yours.")
            if age >= 2:
                bits.append(f"Written {age} days ago — the 2-day rule calls it stale: refresh it or cancel it.")
            M.append(_move(state,
                           "A decision is waiting at your broker" if age < 2
                           else "An old plan is about to expire — decide",
                           "  ".join(bits),
                           f"indtrader fill {sym} BUY {qty:g} <your price> --plan {pid}   "
                           f"(only after you actually buy)"))
    else:
        M.append(_move("green", "Nothing waiting on you today",
                       "No open plans. The evening flow decides tomorrow's list: prices are fetched, "
                       "the scan ranks the universe, drafts turn the shortlist into written plans — "
                       "and you approve or reject them at the broker.",
                       "indtrader scan  →  indtrader drafts --top 3  →  indtrader brief (next morning)"))

    if out["problems"]:
        M.append(_move("amber", f"{len(out['problems'])} journal gap(s) — fills without a written plan",
                       "You bought these according to the ledger, but no plan recorded why. "
                       f"{'; '.join(out['problems'][:5])}. The rule is 'no reason, no plan' — "
                       "write the reason now so the review can grade the decision later.",
                       "indtrader plan <SYMBOL> BUY --qty N --entry E --stop S --target T --reason \"...\""))

    if out["split_gaps"] or out["unresolved_ca"]:
        why, cmds = [], []
        if out["split_gaps"]:
            syms = [r[0] for r in out["split_gaps"][:5]]
            why.append(f"{len(out['split_gaps'])} symbols have an unexplained price cliff — "
                       f"real splits not yet corrected{' (' + ', '.join(syms) + ' …)' if len(out['split_gaps']) > 5 else ''}")
            cmds.append("indtrader coractions --gap-list --file data/universe.txt")
        if out["unresolved_ca"]:
            why.append(f"{len(out['unresolved_ca'])} corporate actions have no ratio (the exchange's "
                       "text states none — the code will never guess one)")
            r = out["unresolved_ca"][0]
            cmds.append(f"indtrader coractions {r[0]} --set '{r[1]}|{r[2]}|RATIO'   "
                        "(read the ratio off the exchange record; full list under 'Data problems')")
        M.append(_move("amber", "Fix the price history before trusting any backtest",
                       " — until these are corrected, backtests and health reports measure "
                       "fabricated crashes.   " + "  ·  ".join(why),
                       "  ||  ".join(cmds)))

    if out["news"]:
        first = out["news"][0]
        M.append(_move("amber", f"{len(out['news'])} warning-sign headline(s) on your watch",
                       f"Newest: {first[0]} [{first[1]}] {first[2][:100]} — read before your next "
                       "decision on that name.", "open 'Warning signs' below for the full list"))

    if (out["health"] or {}).get("problem"):
        h = out["health"]
        M.append(_move("amber", f"{h['problem']} symbols have bars older than 3 sessions",
                       "The daily fetch did not refresh them (delisting, ticker change, or a flaky "
                       "hour at the exchange). The heatmap shows exactly which.",
                       "indtrader fetch-universe --file data/universe.txt   (then re-run health)"))
    return M


def _pct(x: float) -> str:
    return f"{x:+.1%}"


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


def serve(host: str = "127.0.0.1", port: int = 8765, universe_file=None) -> int:
    from pathlib import Path

    from http.server import ThreadingHTTPServer

    from indtrader.config import get_settings
    from indtrader.data.store import BarStore
    from indtrader.data.universe import load_universe
    from indtrader.execution.planlog import PlanLog

    if host not in ("127.0.0.1", "localhost"):
        # no auth, so LAN exposure needs an explicit flag — the sanctioned case is
        # the Docker service (--host 0.0.0.0 + a port map the operator controls).
        print(f"WARNING: dashboard binding {host} — no auth; only do this where "
              "you control who can reach the port (e.g. Docker port map)")
    # same convention as review/riskreport/briefing: data/universe.txt when it
    # exists (the laptop's 500-name file), the 10-name default only as a labeled
    # bootstrap — the old load_universe(None) here silently showed 10 symbols on
    # a machine holding 500 (laptop screenshot, 2026-11-12).
    uf = Path(universe_file) if universe_file else get_settings().data_dir / "universe.txt"
    universe = load_universe(uf if Path(uf).exists() else None)
    print(f"universe: {len(universe)} symbols from {uf if Path(uf).exists() else 'built-in default (no data/universe.txt)'}")
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
