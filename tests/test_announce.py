from indtrader.data import announce
from indtrader.data.store import BarStore


def test_triage_severity_and_ignorance():
    red = announce.triage_title("Intimation of pledge encroachment received from deposits")
    assert red and red[0] == "RED" and "pledge encroach" in red[1]
    assert announce.triage_title("Forensic audit report has been submitted")[0] == "RED"
    assert announce.triage_title("Credit rating of the company revised")[0] == "AMBER"
    assert announce.triage_title("Board meeting to consider financial results")[0] == "AMBER"
    assert announce.triage_title("5% price deviation intimation") is None      # noise stays noise
    assert announce.triage_title("complained about parking") is None          # word-boundary, not substring


def test_store_and_holdings_only_filter(tmp_path):
    s = BarStore(tmp_path / "b.sqlite")
    n = announce.store_announcements(s, [
        {"symbol": "TATA", "d": "2026-11-08", "title": "Forensic audit appointed"},
        {"symbol": "OTHER", "d": "2026-11-08", "title": "Fraud alleged"},           # not held
        {"symbol": "TATA", "d": "2020-01-01", "title": "Old fraud"},                # outside window
        {"symbol": "TATA", "d": "unknown", "title": "Director resignation"},
    ])
    assert n == 4
    m = announce.triaged_recent(s, ["TATA"], days=30 * 365)  # big window: age filter tested separately
    assert {x.title for x in m} == {"Forensic audit appointed", "Old fraud", "Director resignation"}
    assert "OTHER" not in {x.symbol for x in m}
    assert m[0].severity == "RED"  # RED first
    recent = announce.triaged_recent(s, ["TATA"], days=30)
    assert "Old fraud" not in {x.title for x in recent}   # aged out honestly
    s.close()


def test_no_lib_reports_instead_of_faking(tmp_path):
    s = BarStore(tmp_path / "b.sqlite")
    res = announce.fetch_announcements(["TATA"], s)   # neither nse lib installed here
    assert res["ok"] is False and "NseIndiaApi not installed" in res["error"]
    s.close()


def test_empty_book_gets_all_clear_not_firehose(tmp_path):
    s = BarStore(tmp_path / "b.sqlite")
    assert announce.triaged_recent(s, []) == []
    assert "all-clear" in announce.format_alert([])
    s.close()
