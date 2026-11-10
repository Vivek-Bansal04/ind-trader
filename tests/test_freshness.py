"""Freshness checker behaviour + the real guard: our own src/ markers must be fresh."""
import importlib.util
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_freshness", ROOT / "tools" / "check_freshness.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stale_marker_fails(tmp_path):
    (tmp_path / "rates.py").write_text('X_RATES_AS_OF = "2020-01-01"\n', encoding="utf-8")
    m = _load_checker()
    found = m.scan(tmp_path)
    assert len(found) == 1 and m.stale_items(found, 10)
    assert m.main(["--root", str(tmp_path), "--json"]) == 1


def test_fresh_marker_passes(tmp_path):
    (tmp_path / "rates.py").write_text(
        f'X_RATES_AS_OF = "{date.today().isoformat()}"\n', encoding="utf-8")
    m = _load_checker()
    assert m.stale_items(m.scan(tmp_path), 10) == []
    assert m.main(["--root", str(tmp_path)]) == 0


def test_no_markers_is_a_failure_not_a_pass(tmp_path):
    (tmp_path / "silent.py").write_text("STT_BUY = 0.001\n", encoding="utf-8")
    m = _load_checker()
    assert m.main(["--root", str(tmp_path)]) == 1


def test_shipped_constants_are_current():
    """The guard that matters: real src/ markers within one Finance-Act cycle."""
    m = _load_checker()
    found = m.scan(ROOT / "src")
    assert found, "src/ must carry *_AS_OF markers for every embedded market constant"
    assert m.stale_items(found, 10) == []
