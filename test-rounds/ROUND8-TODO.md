> Moved to `test-rounds/` on 2026-11-12 — edit THIS file where it now lives; the
> paste-back workflow (paste into the file, push it back) is unchanged.

# ROUND 8 — laptop checklist (~20 min). Round 7 closed it: NSE subjects DO
# state ratios, they just wrap words inside the from-to phrase ("From Rs 2 Per
# Share To Rs 1 Per Share"). The parser now reads that spelling, and the first
# batch pass over the 58 gap names is one command. Meanwhile the dashboard got
# its ledger sections and the Docker stack became the whole project — both get
# a real-DB look this round.

Code changes pulled this round (`ae6a1ab` must be in `git log --oneline -3`,
or STOP):

- `coractions` reads the live face-value spelling + "from X share(s) to Y"
  (pinned by tests, incl. refusals: date pairs like "from 01-Apr to 15-Apr"
  are never a ratio). Expect the UNRESOLVED count to drop a lot.
- Dashboard: split-gap ledger, ETL view, freshness heatmap, holiday drift,
  flagged news, shareholding alerts, sparklines — same arithmetic as the CLI.
  Ctrl-C shutdown bug fixed. `--host` exists; default stays 127.0.0.1.
- Docker: `docker compose up -d` now = ETL loop + dashboard; image installs
  `[nse,web]` (curl_cffi included); `.env` optional.

Start with:

```bash
cd ~/Desktop/ind-trader
git pull
git log --oneline -3   # must include ae6a1ab
```

---

## 1. The batch pass — 58 names, one command (~5-10 min, live NSE)

```bash
.venv/bin/python -m indtrader.cli coractions --gap-list --file data/universe.txt 2>&1 | tail -70
.venv/bin/python -m indtrader.cli health --file data/universe.txt | tail -8
```

The gap-list prints one line per symbol (factors stored / UNRESOLVED / no
payload). The health tail afterwards is the ledger we care about: SPLIT-GAP
should shrink hard from 58. Whatever REMAINS is the exchange-PDF + `--set`
worklist — paste those names exactly.

>>> PASTE 1 — last ~30 lines of the gap-list run, then the health tail:
```
```

---

## 2. The dashboard on real data (~5 min)

```bash
.venv/bin/python -m indtrader.cli dashboard
```

Open http://127.0.0.1:8765 — expect: banner, ETL view (fresh count + latest
bar; clean-day streak honestly says "not persisted"), freshness heatmap,
split-gap ledger (matches PASTE 1's names — same helper), UNRESOLVED actions,
holiday ledger (should show the four named drift days), flagged news, book
with sparklines. Then Ctrl-C — it must exit cleanly (no traceback).

>>> PASTE 2 — one screenshot of the page (top), and: any section that looks
>>> wrong or says something you didn't expect — quote it. Plus the factor
>>> values HDFCBANK's stored rows show:
>>>   sqlite3 data/bars.sqlite "SELECT * FROM corporate_actions WHERE symbol='HDFCBANK' ORDER BY d"
>>>   sqlite3 data/bars.sqlite "SELECT * FROM split_factors WHERE symbol='HDFCBANK'"
>>> (repo convention: a 1:1 bonus stores 0.5 — the price halves. A 2.0 in
>>> there came from an older experiment and should be corrected via --set.)
```
```

---

## 3. Docker whole-project check (optional, ~10 min — Docker Desktop installed?)

```bash
docker compose up -d          # etl-loop + dashboard, from ae6a1ab
docker compose run --rm cli health --file /home/appuser/data/universe.txt | tail -3
docker logs indtrader-etl --tail 5
curl -s http://127.0.0.1:8765/api/status | head -c 300
docker compose down
```

Note: the container's ./data volume starts EMPTY unless you point it at the
real folder (edit the compose volume line to your actual data dir, or run
`etl-once` inside to build fresh). Either answer is fine — what we need is
whether the image builds and the two services come up on a Mac.

>>> PASTE 3 — build result + the curl head + any error verbatim (or "no Docker"):
```
```

---

## What each answer decides

| # | Question it settles |
|---|---------------------|
| 1 | How many names the spelling fix closed; the rest become `--set` work from exchange PDFs |
| 2 | Whether the page tells the same story as the CLI (it must) |
| 3 | Whether "docker compose up" is now the whole-project answer on the real runtime box |

Done = push this file back. "Didn't run" is a valid answer, a fake paste is not.
