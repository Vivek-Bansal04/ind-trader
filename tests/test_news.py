import pytest

from indtrader.data import news
from indtrader.data.store import BarStore

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>q</title>
  <item><title>Acme promoter pledge encroachment notice filed</title>
        <link>https://ex.com/a1</link><pubDate>Mon, 10 Nov 2026 09:00:00 GMT</pubDate></item>
  <item><title>Acme board meeting on Q3 results</title>
        <link>https://ex.com/a2</link><pubDate>Mon, 10 Nov 2026 10:00:00 GMT</pubDate></item>
  <item><title>Cricket score update</title>
        <link>https://ex.com/a3</link><pubDate>Mon, 10 Nov 2026 11:00:00 GMT</pubDate></item>
  <item><title>row with no link must be skipped, not stored</title>
        <pubDate>Mon, 10 Nov 2026 12:00:00 GMT</pubDate></item>
</channel></rss>"""


@pytest.fixture
def store(tmp_path):
    s = BarStore(tmp_path / "b.sqlite")
    yield s
    s.close()


# ---- fetch_feed: parsing AND honest failure shapes ---------------------------------

def test_fetch_feed_parses_items_and_counts_skips(monkeypatch):
    class FakeResp:
        def read(self):
            return RSS_FIXTURE.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(news.urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    items, skipped, err = news.fetch_feed("http://anything")
    assert err is None and skipped == 1           # the linkless row, COUNTED
    assert [i["title"][:4] for i in items] == ["Acme", "Acme", "Cric"]


def test_fetch_feed_network_error_is_reported_not_empty(monkeypatch):
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.URLError("403: office proxy")

    monkeypatch.setattr(news.urllib.request, "urlopen", boom)
    items, skipped, err = news.fetch_feed("http://anything")
    assert items == [] and err and "URLError" in err   # outage, never a quiet zero


def test_fetch_feed_malformed_xml_reports_parse_error(monkeypatch):
    class FakeResp:
        def read(self):
            return b"<rss><broken"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(news.urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    items, skipped, err = news.fetch_feed("http://anything")
    assert items == [] and "ParseError" in err


# ---- store + triage ------------------------------------------------------------------

def test_store_news_is_idempotent_by_link(store):
    items = [{"title": "X fraud charge", "link": "https://e/1", "pubDate": ""}]
    assert news.store_news(store, "X", items, "gnews") == 1
    assert news.store_news(store, "X", items, "gnews") == 0   # nightly re-runs cost nothing


def test_triage_uses_the_announce_vocabulary(store):
    news.store_news(store, "ACME", [
        {"title": "ACME promoter pledge encroachment", "link": "l1", "pubDate": ""},  # RED
        {"title": "ACME board meeting for Q3 results", "link": "l2", "pubDate": ""},  # AMBER
        {"title": "ACME launches new showroom", "link": "l3", "pubDate": ""},         # none
    ], "gnews")
    m = news.triaged_recent(store, ["ACME", "MARKET"])
    assert [x.severity for x in m] == ["RED", "AMBER"]     # RED first, same terms as announcements
    assert all("showroom" not in x.title for x in m)       # unflagged never surfaces
    assert news.triaged_recent(store, []) == []            # holdings-only rule holds


def test_fetch_news_reports_quiet_day_versus_outage(store, monkeypatch):
    # all queries error -> ok=False (outage). partial -> ok=True + named failures.
    monkeypatch.setattr(news, "fetch_feed", lambda url: ([], 0, "URLError: down"))
    res = news.fetch_news(["ACME"], store)
    assert res["ok"] is False and len(res["unreachable"]) == 2   # MARKET + ACME both dead

    def flaky(url):
        if "ACME" in url:
            return ([{"title": "ACME credit rating downgrade", "link": "l9", "pubDate": ""}], 0, None)
        return [], 0, "HTTPError: 429"

    monkeypatch.setattr(news, "fetch_feed", flaky)
    res = news.fetch_news(["ACME"], store)
    assert res["ok"] and res["stored_new"] == 1 and any("MARKET" in u for u in res["unreachable"])


# ---- CLI (offline: stored rows only) ---------------------------------------------------

def test_cli_news_offline_shows_triage(store, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    from indtrader.cli import main

    news.store_news(BarStore(tmp_path / "bars.sqlite"), "ACME",
                    [{"title": "ACME forensic audit ordered", "link": "l1", "pubDate": ""}], "gnews")
    rc = main(["news", "ACME", "--no-fetch"])              # offline: zero network in tests
    out = capsys.readouterr().out
    get_settings.cache_clear()
    assert rc == 0 and "[RED] ACME" in out


def test_cli_news_reports_total_outage_as_unknown(tmp_path, monkeypatch, capsys):
    """All-feed-failure must print UNKNOWN-not-quiet and exit 1 — a dead feed
    dressed as a calm evening is the one unforgivable output here."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    from indtrader.data import news as news_mod

    monkeypatch.setattr(news_mod, "fetch_feed", lambda url: ([], 0, "URLError: down"))
    from indtrader.cli import main

    rc = main(["news", "ACME"])
    err = capsys.readouterr().err
    get_settings.cache_clear()
    assert rc == 1 and "UNKNOWN, not quiet" in err
