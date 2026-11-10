"""Universe = plain text files (symbol<TAB>sector), versioned in git.
/tmp_nifty200_entries.txt from stock-analyzer graduates into a real file here in Phase 2."""
from __future__ import annotations

from pathlib import Path

DEFAULT_UNIVERSE = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "ITC", "LT", "BHARTIARTL", "MARUTI"]


def load_universe(path: Path | None) -> list[str]:
    if path is None or not Path(path).exists():
        return list(DEFAULT_UNIVERSE)
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.split("\t")[0].strip())
    return out
