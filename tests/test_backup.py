import sqlite3

from indtrader.execution.planlog import PlanLog
from indtrader.scheduler.backup import backup_all, prune, restore


def make_db(path, rows=3):
    import sqlite3

    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE t(x)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    con.close()


def test_backup_writes_verifiable_copies(tmp_path):
    a = tmp_path / "bars.sqlite"
    make_db(a)
    res = backup_all([a, tmp_path / "missing.sqlite"], tmp_path / "bk", stamp="20261111-010101")
    assert len(res["written"]) == 1 and res["failed"] == []      # missing source is skipped, not faked
    con = sqlite3.connect(res["written"][0])
    assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    con.close()


def test_prune_keeps_newest(tmp_path):
    bk = tmp_path / "bk"
    bk.mkdir()
    for i in range(5):
        f = bk / f"bars-2026111{i}-010101.sqlite"
        f.write_bytes(b"x")
    removed = prune(bk, keep=2)
    assert len(removed) == 3 and (bk / "bars-20261114-010101.sqlite").exists()


def test_restore_roundtrip_and_corruption_refusal(tmp_path):
    src = tmp_path / "plans.sqlite"
    make_db(src, rows=7)
    res = backup_all([src], tmp_path / "bk", stamp="s1")
    live = tmp_path / "live" / "plans.sqlite"
    live.parent.mkdir()
    make_db(live, rows=1)
    out = restore(res["written"][0], live)
    con = sqlite3.connect(out)
    assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 7
    con.close()
    assert live.with_suffix(".sqlite.pre-restore").exists()      # previous copy kept

    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"not a database")
    try:
        restore(bad, tmp_path / "never-written.sqlite")
        assert False, "should refuse corrupt backup"
    except sqlite3.DatabaseError:
        pass
    assert not (tmp_path / "never-written.sqlite").exists()


def test_backup_command_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    PlanLog().close()                                            # creates plans.sqlite
    from indtrader.cli import main

    assert main(["backup"]) == 0
    out = capsys.readouterr().out
    assert "plans-" in out and '"failed": []' in out
    get_settings.cache_clear()
