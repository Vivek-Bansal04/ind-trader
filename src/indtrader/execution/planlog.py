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
"""


class PlanLog:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else Path("data/plans.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path))
        self.con.executescript(SCHEMA)

    def record_signal(self, symbol: str, source: str, payload: dict) -> None:
        self.con.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?)".replace("?,?,", "?,?,?,").replace("?,)", "?)"),
            (datetime.utcnow(), symbol.upper(), source, json.dumps(payload, default=str)),
        )
        self.con.commit()

    def add_plan(self, plan: OrderPlan) -> int:
        cur = self.con.execute(
            "INSERT INTO plans(created,symbol,action,qty,entry,stop,target,reason,setup) VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.utcnow(), plan.symbol.upper(), plan.action.upper(), plan.qty, plan.entry, plan.stop,
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

    def close(self):
        self.con.close()
