"""Paper-loop glue (Phase-4 discipline as ONE flow, no new truths).

The evening chain: fetch -> scan -> DRAFT plans -> tomorrow's MORNING BRIEF ->
the human trades at the broker -> `fill --plan` closes it -> review/decay read
the journal. Every part already existed as a separate command; what was missing
was the connective tissue — and the rule it must not break: a draft is a PROPOSAL.
Nothing here can execute; the journal only ever records what the human already
did. Numbers come from the SAME sizing/limits/events functions the live path
uses — the draft and the real risk check can never disagree.

Sizing here mirrors `cmd_plan`'s front-door refusals (0 < stop < entry < target,
>= 10-char reason) so a draft that reaches the journal is a plan the CLI would
have accepted. Entry/stop/target use the SAME 3.0 x ATR14 stop the backtest
engine trails — plan-vs-actual compares like with like.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from indtrader.portfolio.sizing import size_position
from indtrader.research import events
from indtrader.ta import atr

STOP_ATR_MULT = 3.0     # identical to backtest.engine — plan and backtest agree
TARGET_R = 2.0          # target = 2R (stop distance twice); a default to argue with,
                        # not a rule — the brief shows it, the human edits the plan.


@dataclass
class DraftPlan:
    symbol: str
    qty: int
    entry: float
    stop: float
    target: float
    reason: str
    skip: str = ""      # non-empty = no draft, with the human-readable why


def draft_for(store, sym: str, equity: float, cash: float | None = None,
              index: str = "NIFTY") -> DraftPlan:
    """One evidence-backed draft from CLOSED bars. Every refusal names itself;
    no qty is ever invented to fill a slot."""
    df = store.load(sym)
    if df is None or df.empty or len(df) < 211:  # the scanner's own 200-DMA gate
        n = 0 if df is None else len(df)
        return DraftPlan(sym, 0, 0, 0, 0, "", skip=f"only {n} bars in store")
    close = float(df["close"].iloc[-1])
    a = float(atr(df, 14).iloc[-1])
    if pd.isna(a) or a <= 0:
        return DraftPlan(sym, 0, 0, 0, 0, "", skip="ATR14 uncomputable — cannot size")
    sized = size_position(equity, price=close, atr_value=a, cash=cash,
                          stop_atr_mult=STOP_ATR_MULT)
    if sized.qty <= 0:
        return DraftPlan(sym, 0, 0, 0, 0, "", skip=f"sizing refused: {sized.reason}")
    qty, note = events.size_gate(store, sym, sized.qty, index)
    if qty <= 0:
        return DraftPlan(sym, 0, 0, 0, 0, "", skip=note)
    stop = round(close - STOP_ATR_MULT * a, 2)
    if stop <= 0:
        return DraftPlan(sym, 0, 0, 0, 0, "", skip="stop hits zero — name too volatile for 3ATR stop")
    risk = close - stop
    target = round(close + TARGET_R * risk, 2)
    s200 = float(df["close"].rolling(200, min_periods=200).mean().iloc[-1])
    reason = (f"trend: close {close:.1f} > 200dma {s200:.1f}; ATR14 {a:.1f}; "
              f"risk {sized.reason}; {note} — DRAFT for human review")
    d = DraftPlan(sym.upper(), int(qty), round(close, 2), stop, target, reason)
    if qty != sized.qty:  # half-size note must ride INTO the journal, not just the screen
        d.reason += f" (results rule: {sized.qty} -> {qty} qty)"
    return d


def morning_brief(store, log, index: str = "NIFTY") -> str:
    """Tomorrow-morning text: stale first, then each OPEN plan against CLOSED
    bars + the event rule. Advice-shaped words (buy/sell) never appear — this
    prints facts the human decides on."""
    L = [f"morning brief — {pd.Timestamp.today().date()} (all evidence = CLOSED bars)"]
    expired = log.expire_stale(pd.Timestamp.today().date())
    rows = log.open_plans()
    if expired:
        L.append(f"expired overnight (TTL): {expired} plan(s) marked stale — a stale signal is a cancelled signal")
    if not rows:
        L.append("no open plans — evening chain: fetch-universe -> scan -> drafts --apply")
        return "\n".join(L)
    held = log.executed_positions()
    dead = False
    for r in rows:
        # plans column order: id, created, symbol, action, qty, entry, stop, target, ...
        pid, sym, qty, entry, stop, target = r[0], str(r[2]), r[4], r[5], r[6], r[7]
        df = store.load(sym)
        if df is None or df.empty:
            L.append(f"#{pid} {sym} qty {qty:g} entry {entry} stop {stop} target {target}"
                     " — NO DATA: unknown is not safe, fetch before deciding")
            continue
        last = float(df["close"].iloc[-1])
        last_d = pd.Timestamp(df.index[-1]).date()
        idx = store.load(index)
        if idx is not None and not idx.empty and \
                pd.Timestamp(idx.index[-1]).date() != last_d:
            L.append(f"#{pid} {sym}: DATA STALE — last {sym} bar {last_d} vs index calendar; fix ETL first")
            dead = True
            continue
        gap = last / entry - 1.0
        tag = "gap UP" if gap > 0.01 else "gap DOWN" if gap < -0.01 else "flat vs entry"
        allowed, note = events.size_gate(store, sym, int(qty), index)
        bits = [f"#{pid} {sym} qty {qty:g} entry {entry} stop {stop} target {target}",
                f"last close {last:.2f} ({last_d}) {tag} {gap:+.1%}"]
        if allowed <= 0:
            bits.append(f"BLOCKED by plan rule: {note}")
        elif note:
            bits.append(note + ("" if allowed == int(qty) else f" -> act on qty {allowed}"))
        if last <= stop:
            bits.append("close already BELOW stop — thesis check before ANY action")
        if sym.upper() in held:
            bits.append(f"journal already shows {held[sym.upper()]:g} executed — no duplicate")
        L.append(" | ".join(bits))
    if dead:
        L.append("DEAD-MAN: at least one row above was measured on stale bars — that row is UNKNOWN, not a signal.")
    return "\n".join(L)
