"""Telegram COMMAND side (plan section E) — long-polling, no public webhook.

Guardrails, in order of importance:
1. It can never place orders: COMMANDS is the exhaustive allowlist and contains
   only reads and journal writes (a /fill records a human decision ALREADY made in
   the broker). A test asserts no order-verb ever appears among the commands.
2. Chat-id pinning BEFORE parsing: an update from any other chat is dropped without
   acting or revealing anything.
3. Long-poll only (getUpdates): no exposed port, no URL to attack.
The one-way sender in telegram.py stays as-is; this module USES it for replies.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from indtrader.config import Settings, get_settings

API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 35.0  # long-poll seconds held server-side
REQ_TIMEOUT = 45.0


@dataclass
class Update:
    update_id: int
    chat_id: str
    text: str


# ---------------- pure command layer (no network; unit-testable) ----------------

def parse_update(raw: dict) -> Update | None:
    try:
        msg = raw["message"]
        return Update(int(raw["update_id"]), str(msg["chat"]["id"]), (msg.get("text") or "").strip())
    except (KeyError, TypeError, ValueError):
        return None


def parse_cmd(text: str) -> tuple[str, list[str]]:
    t = text.lstrip("/").split()  # argv keeps its case (symbols/args), command name doesn't
    return (t[0].lower(), t[1:]) if t else ("", [])


def check_auth(chat_id: str, s: Settings) -> bool:
    return bool(s.telegram_chat_id.strip()) and str(chat_id).strip() == str(s.telegram_chat_id).strip()


COMMANDS: dict = {}


def command(name: str):
    def deco(fn):
        COMMANDS[name] = fn
        return fn
    return deco


def run_command(text: str, s: Settings | None = None) -> str:
    """Dispatch a (trusted) command's text -> reply text. Never raises past here."""
    s = s or get_settings()
    name, argv = parse_cmd(text)
    if name in ("", "start", "help"):
        return _help()
    fn = COMMANDS.get(name)
    if fn is None:
        return f"unknown command '/{name}' — try /help (nothing was executed)"
    try:
        return fn(argv, s) or "done"
    except Exception as e:
        return f"command /{name} failed: {type(e).__name__}: {str(e)[:200]}"


def _help() -> str:
    return ("ind-trader bot — reads + journal only, it can never place orders.\n"
            "/status  regime + book + loop state\n/book  holdings at last marks\n/scan  last shortlist\n"
            "/events  upcoming results\n/ann  flagged announcements (holdings)\n"
            "/fill SYM BUY|SELL QTY PX [COSTS]  record a fill you ALREADY made\n"
            "/pause | /resume  quiet the daily outbound loop")


# ---------------- command implementations (local stores only) ----------------

def _fmt(x, spec=",.0f") -> str:
    return "unknown" if x is None else format(x, spec)


@command("status")
def _status(argv, s):
    from indtrader.alerts.pause import is_paused
    from indtrader.data.store import BarStore
    from indtrader.execution.planlog import PlanLog

    lines = []
    store, log = BarStore(), PlanLog()
    try:
        idx = store.load("NIFTY")
        if idx.empty:
            lines.append("NIFTY bars missing — ETL hasn't run?")
        else:
            from indtrader.data.universe import load_universe
            from indtrader.research.regime import regime_from_data

            closes = {}
            for sym in load_universe(s.data_dir / "universe.txt"):
                df = store.load(sym)
                if not df.empty and sym != "NIFTY":
                    closes[sym] = df["close"]
            if closes:
                r = regime_from_data(closes, idx["close"], idx["volume"])
                lines.append(f"Regime {r.label} exposure {r.exposure_scale:g} "
                             f"breadth {'unknown' if r.breadth is None else format(r.breadth, '.0%')}")
            last = idx["close"].iloc[-1]
            wk = idx["close"].tail(6)
            chg = float(last / wk.iloc[0] - 1) if len(wk) >= 2 else 0.0
            lines.append(f"NIFTY {float(last):,.0f} ({chg:+.2%} 5d) at {idx.index[-1].date()}")
        lines.append(_book_line(store, log))
        n_open = len(log.open_plans())
        etl = store.con.execute("SELECT max(run_at) FROM ingest_runs").fetchone()[0]
        lines.append(f"{n_open} open plans | last ETL {etl or 'never'} | loop "
                     + ("PAUSED (/resume)" if is_paused(s) else "active"))
    finally:
        store.close()
        log.close()
    return "\n".join(lines)


def _book_line(store, log) -> str:
    from indtrader.portfolio.valuation import book_xirr, held_quantities, resolve_marks

    fills = log.fills()
    if not fills:
        return "Book: no fills yet"
    marks = resolve_marks(store, held_quantities(fills))
    rep = book_xirr(fills, marks)
    if not rep["ok"]:
        return f"Book: XIRR unavailable — {rep['reason']}"
    return (f"Book: XIRR {'unbounded' if rep['xirr'] is None else format(rep['xirr'], '.1%')}"
            f" | open {rep['open_value']:,.0f} | {len(held_quantities(fills))} positions")


@command("book")
def _book(argv, s):
    from indtrader.data.store import BarStore
    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.valuation import held_quantities

    store, log = BarStore(), PlanLog()
    try:
        fills = log.fills()
        held = held_quantities(fills)
        if not held:
            return "Book is empty (no fills recorded)"
        lines = [_book_line(store, log)]
        from indtrader.portfolio.valuation import resolve_marks

        marks = resolve_marks(store, sorted(held))
        for sym, q in sorted(held.items()):
            px = marks.get(sym)
            lines.append(f"{sym} {q} @ {_fmt(px)}")
        return "\n".join(lines)
    finally:
        store.close()
        log.close()


@command("scan")
def _scan(argv, s):
    from indtrader.data.store import BarStore
    from indtrader.execution.planlog import PlanLog

    log = PlanLog()
    try:
        rows = log.con.execute(
            "SELECT ts, payload FROM signals WHERE source='scan' "
            "ORDER BY ts DESC LIMIT 1").fetchall()
    finally:
        log.close()
    if not rows:
        return "no scan journaled yet (run: indtrader scan)"
    import json

    created, payload = rows[0]
    try:
        data = json.loads(payload)
    except ValueError:
        return f"latest scan ({created}) payload unreadable — check plans.sqlite"
    items = data.get("picks", []) if isinstance(data, dict) else data
    out = [f"scan {created} regime {data.get('regime', '?')} "
           f"(top {min(len(items), 10)}):"] if isinstance(data, dict) else [f"scan {created}:"]
    for r in items[:10]:
        if isinstance(r, dict):
            out.append(f"{r.get('symbol')} score {r.get('score')} q={r.get('quadrant', '-')} "
                       + (f"| {r['note']}" if r.get("note") else ""))
    return "\n".join(out) if len(out) > 1 else "scan row list was empty"


@command("events")
def _events(argv, s):
    from indtrader.data.store import BarStore

    store = BarStore()
    try:
        has = store.con.execute("SELECT name FROM sqlite_master WHERE name='events'").fetchone()
        rows = store.con.execute(
            "SELECT symbol, d FROM events WHERE kind='results' AND d >= date('now') ORDER BY d LIMIT 12"
        ).fetchall() if has else []
        return "upcoming results: " + (", ".join(f"{a} {b}" for a, b in rows) or "none stored")
    finally:
        store.close()


@command("ann")
def _ann(argv, s):
    from indtrader.data.announce import format_alert, triaged_recent
    from indtrader.data.store import BarStore
    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.valuation import held_quantities

    log = PlanLog()
    try:
        held = sorted(held_quantities(log.fills()))
    finally:
        log.close()
    store = BarStore()
    try:
        return format_alert(triaged_recent(store, held, days=7))
    finally:
        store.close()


@command("fill")
def _fill(argv, s):
    """RECORDS a fill the human already executed at the broker. Writing this journal
    row is not, and cannot become, an order path — there is no broker call anywhere."""
    from indtrader.execution.planlog import PlanLog

    if len(argv) < 5:
        return "usage: /fill SYM BUY|SELL QTY PX [COSTS]"
    sym, side, qty_s, px_s = argv[0], argv[1].upper(), argv[2], argv[3]
    if side not in ("BUY", "SELL"):
        return "side must be BUY or SELL"
    try:
        qty, px = int(qty_s), float(px_s)
        costs = float(argv[4]) if len(argv) > 4 else 0.0
    except ValueError:
        return "qty/price/costs must be numbers"
    if qty <= 0 or px <= 0 or costs < 0:
        return "qty and price must be positive, costs non-negative"
    from datetime import date

    log = PlanLog()
    try:
        fid = log.add_fill(date.today(), sym, side, qty, px, costs, note="via telegram bot")
        return f"recorded fill #{fid}: {side} {qty} {sym.upper()} @ {px:g} (costs {costs:g})"
    except Exception as e:
        return f"fill rejected: {type(e).__name__}: {str(e)[:150]}"
    finally:
        log.close()


@command("pause")
def _pause(argv, s):
    from indtrader.alerts.pause import set_paused

    set_paused(True, "paused via telegram /pause", s)
    return "daily outbound loop paused (briefings won't send; /resume to re-enable)"


@command("resume")
def _resume(argv, s):
    from indtrader.alerts.pause import set_paused

    set_paused(False, settings=s)
    return "loop resumed — next briefing will send as configured"


# ---------------- network shell (thin; injected transport in tests) ----------------

def get_updates(token: str, offset: int | None) -> tuple[int, list[dict]]:
    """One long-poll. On any failure back off and return (offset, []) — never raise
    past here; the loop must survive NSE-style outages and bad tokens alike."""
    try:
        r = httpx.get(API.format(token=token, method="getUpdates"),
                      params={"offset": offset, "timeout": int(POLL_TIMEOUT)},
                      timeout=REQ_TIMEOUT)
        if r.status_code == 409:  # another webhook/poller registered — say it plainly, back off
            print("[bot] getUpdates 409 conflict — a webhook or another poller owns this token; "
                  "clear it via the Bot API deleteWebhook (or use a fresh bot token)")
            time.sleep(30.0)
            return offset, []
        data = r.json()
        if not data.get("ok"):
            return offset, []
        ups = data.get("result", [])
        nxt = max((int(u.get("update_id", 0)) for u in ups), default=offset or 0)
        return (nxt + 1 if ups else (offset or 1)), ups
    except Exception:
        time.sleep(2.0)
        return offset, []


def process_once(offset: int | None, s: Settings,
                 fetch=get_updates, reply=None) -> int | None:
    """One poll+dispatch cycle (also the test seam). Returns the new offset."""
    from indtrader.alerts.telegram import send

    reply = reply or (lambda text: send(text, s))
    nxt, ups = fetch(s.telegram_bot_token.strip(), offset)
    for raw in ups:
        u = parse_update(raw)
        if u is None:
            continue
        if not check_auth(u.chat_id, s):
            continue  # wrong chat: dropped before parsing — no reply, no signal
        reply(run_command(u.text, s))
    return nxt


def poll_loop(s: Settings | None = None) -> int:
    s = s or get_settings()
    if not (s.telegram_bot_token.strip() and s.telegram_chat_id.strip()):
        print("bot needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (the OWNER's chat id)")
        return 1
    print("bot polling (Ctrl+C to stop) — reads + journal only, no order path exists")
    offset = None
    while True:
        try:
            offset = process_once(offset, s)
        except KeyboardInterrupt:
            print("\nstopped")
            return 0
        except Exception as e:  # loop survives anything else
            print(f"[bot] cycle error: {type(e).__name__}: {str(e)[:150]}")
            time.sleep(3.0)
