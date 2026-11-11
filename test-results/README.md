# HOME-VALIDATION test results

Run on: macOS, Python 3.13.1, repo at `~/Desktop/ind-trader` Command: `.venv/bin/python -m pytest -v` Result: **146 passed, 1 failed** (exit code 1)

See `pytest-full.log` for full verbose output.

## The 1 failure

`tests/test_announce.py::test_no_lib_reports_instead_of_faking`

This test asserts that when the `nse` (NseIndiaApi) library is **not installed**, `fetch_announcements` returns `{"ok": False, ...}`.

On this machine `nse` **is** installed (needed for HOME-VALIDATION steps 1, 2, 3, 5), so the function correctly takes the live path and returns `{"ok": True, ...}` instead. The no-lib path itself was verified to work:

```
WITH nse installed   -> {'ok': True, 'method': 'announcements', 'stored': 0, 'failed': []}
WITHOUT nse installed -> {'ok': False, 'error': "NseIndiaApi not installed ..."}
```

This is an environment mismatch, not a code bug. The underlying `TypeError` crash (`NSE()` missing `download_folder`) was fixed in `src/indtrader/data/announce.py`.

## Changes kept

-   `src/indtrader/data/announce.py` — fixed `NSE()` to pass `download_folder` (real bug fix requested by user).

## Testing artifacts removed

Untracked files created during validation runs were cleaned up (see cleanup steps below):

-   `data/bars.sqlite`, `data/universe50.txt`, `data/universe.txt`, `fetch_universe.log`
-   `.pytest_cache/`, `__pycache__/` directories