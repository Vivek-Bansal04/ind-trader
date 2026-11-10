"""Home-network probe: which data sources are actually reachable from here?

Run on a personal/home network (office proxies TLS-intercept NSE/Yahoo):
    python tools/net_probe.py
Prints one line per source with PASS/FAIL. No secrets, read-only GETs only.
"""
from __future__ import annotations

import json
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def get(url: str, timeout: int = 20) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, f"HTTP {r.status}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:90]}"


def main() -> int:
    checks = [
        ("yahoo-chart", "https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=5d&interval=1d"),
        ("nse-home", "https://www.nseindia.com"),
    ]
    ok_all = True
    for name, url in checks:
        ok, msg = get(url)
        ok_all &= ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:14s} {msg}")

    # yfinance end-to-end (uses the chart API through its own session handling)
    try:
        import yfinance as yf
        h = yf.Ticker("RELIANCE.NS").history(period="5d")
        print(f"{'PASS' if not h.empty else 'FAIL'}  {'yfinance-lib':14s} {len(h)} rows"
              + (f", cols include Adj Close: {'Adj Close' in h.columns}" if not h.empty else ""))
    except Exception as e:
        ok_all = False
        print(f"FAIL  {'yfinance-lib':14s} {type(e).__name__}: {str(e)[:90]}")

    # NseIndiaApi (pip extra 'nse') — real archive download, small range
    try:
        from indtrader.data.nseapi_provider import NseIndiaApiProvider
        if NseIndiaApiProvider.available():
            df = NseIndiaApiProvider().daily("RELIANCE", "2026-10-01", "2026-10-15")
            print(f"{'PASS' if df is not None and not df.empty else 'FAIL'}  {'nseapi-arch':14s} "
                  f"{0 if df is None else len(df)} rows")
        else:
            print("SKIP  nseapi-arch    NseIndiaApi not installed (pip install 'indtrader[nse]')")
    except Exception as e:
        print(f"FAIL  nseapi-arch    {type(e).__name__}: {str(e)[:90]}")

    print(json.dumps({"all_core_pass": ok_all}))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
