"""Staleness checker for embedded market constants (concept from claude-trading-skills-india).

Indian rates/slabs/tick rules change by Finance Act and NSE circulars. Every embedded
constant in src/ must carry an `X_AS_OF = "YYYY-MM-DD"` marker; this tool fails when any
marker is older than --max-age-months (default 10 — covers one Finance Act cycle). The
freshness workflow runs it monthly and opens an issue when stale, so old STT/tax numbers
never sit silently inside fresh-looking output.

Usage:  python tools/check_freshness.py [--root src] [--max-age-months 10] [--json]
Exit:   0 fresh / 1 stale or bad-format (stdlib only; no third-party imports).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

AS_OF_RE = re.compile(r'^\s*([A-Z][A-Z0-9_]*_AS_OF)\s*=\s*"(\d{4}-\d{2}-\d{2})"\s*(?:#.*)?$', re.M)


def scan(root: Path) -> list[dict]:
    found = []
    for py in sorted(root.rglob("*.py")):
        text = py.read_text(encoding="utf-8", errors="replace")
        for m in AS_OF_RE.finditer(text):
            name, iso = m.group(1), m.group(2)
            try:
                d = date.fromisoformat(iso)
            except ValueError:  # unreachable given the regex, but be paranoid not silent
                found.append({"name": name, "path": str(py), "as_of": iso, "error": "unparseable date"})
                continue
            found.append({"name": name, "path": py.name, "as_of": iso,
                          "age_days": (date.today() - d).days})
    return found


def stale_items(found: list[dict], max_age_months: int) -> list[dict]:
    limit_days = max_age_months * 30  # month approximation is fine for a 10-month window
    return [f for f in found if "error" in f or f["age_days"] > limit_days]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent / "src"))
    ap.add_argument("--max-age-months", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    found = scan(Path(args.root))
    if not found:
        print("check_freshness: no *_AS_OF markers found — that is itself a failure", file=sys.stderr)
        return 1
    stale = stale_items(found, args.max_age_months)
    if args.json:
        print(json.dumps({"checked": found, "stale": stale, "max_age_months": args.max_age_months}))
    else:
        for f in found:
            mark = "STALE" if f in stale else "ok"
            print(f"  [{mark}] {f['name']} @ {f['path']} as_of={f['as_of']} age={f.get('age_days', '?')}d")
    if stale:
        print(f"check_freshness: {len(stale)} stale constant(s) — verify against current "
              "NSE/SEBI/Finance-Act schedules and bump the *_AS_OF dates.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
