"""The audit journal: every signal, plan, and outcome in SQLite. If it's not here, it
didn't happen. Plans carry mandatory rationale (stock-india practice worth copying)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from indtrader.models import OrderPlan

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals(
    ts TIMESTAMP, symbol TEXT, source TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS plans(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TIMESTAMP, symbol TEXT, action TEXT, qty INT,
    entry REAL, stop REAL, target REAL, reason TEXT, setup TEXT,
    status TEXT DEFAULT 'open'  -- open|executed|cancelled|stale
);
CREATE TABLE IF NOT EXISTS ledger(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dt TEXT, symbol TEXT, side TEXT,  -- BUY | SELL (actual fills, not plans)
    qty INT, price REAL, costs REAL DEFAULT 0, note TEXT
);
"""


class PlanLog:
    def __init__(self, path: Path | None = None):
        if path is None:
            from indtrader.config import get_settings

            path = get_settings().data_dir / "plans.sqlite"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path))
        self.con.executescript(SCHEMA)

    def record_signal(self, symbol: str, source: str, payload: dict) -> None:
        self.con.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?)".replace("?,?,", "?,?,?,").replace("?,)", "?)"),
            (datetime.utcnow().isoformat(sep=" "), symbol.upper(), source, json.dumps(payload, default=str)),
        )
        self.con.commit()

    def add_plan(self, plan: OrderPlan) -> int:
        cur = self.con.execute(
            "INSERT INTO plans(created,symbol,action,qty,entry,stop,target,reason,setup) VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(sep=" "), plan.symbol.upper(), plan.action.upper(), plan.qty, plan.entry, plan.stop,
             plan.target, plan.reason, plan.setup),
        )
        self.con.commit()
        return int(cur.lastrowid)

    def mark(self, plan_id: int, status: str) -> None:
        self.con.execute("UPDATE plans SET status=? WHERE id=?", [status, plan_id])
        self.con.commit()

    def expire_stale(self, today: date, ttl_days: int = 2) -> int:
        cur = self.con.execute(
            "UPDATE plans SET status='stale' WHERE status='open' AND julianday('now') - julianday(created) > ?",
            [ttl_days],
        )
        self.con.commit()
        return cur.rowcount

    def open_plans(self):
        return self.con.execute("SELECT * FROM plans WHERE status='open' ORDER BY created").fetchall()

    def add_fill(self, dt, symbol: str, side: str, qty: int, price: float,
                 costs: float = 0.0, note: str = "") -> int:
        cur = self.con.execute(
            "INSERT INTO ledger(dt,symbol,side,qty,price,costs,note) VALUES (?,?,?,?,?,?,?)",
            (str(dt), symbol.upper(), side.upper(), int(qty), float(price), float(costs), note),
        )
        self.con.commit()
        return int(cur.lastrowid)

    def fills(self):
        rows = self.con.execute(
            "SELECT dt,symbol,side,qty,price,costs FROM ledger ORDER BY dt, id"
        ).fetchall()
        from indtrader.portfolio.tax import LedgerEntry
        return [LedgerEntry(date.fromisoformat(str(r[0])[:10]), r[1], r[2], int(r[3]), float(r[4]), float(r[5]))
                for r in rows]

    def executed_positions(self) -> dict[str, int]:
        """Net quantity per symbol implied by EXECUTED plans (BUY +, SELL -).
        This is the journal's view of intent that actually happened — reconcile
        against broker holdings to find where reality diverged."""
        rows = self.con.execute(
            "SELECT symbol, action, qty FROM plans WHERE status='executed'"
        ).fetchall()
        out: dict[str, int] = {}
        for symbol, action, qty in rows:
            delta = int(qty) if str(action).upper() == "BUY" else -int(qty)
            out[symbol] = out.get(symbol, 0) + delta
        return {s: q for s, q in out.items() if q != 0}

    def close(self):
        self.con.close()
