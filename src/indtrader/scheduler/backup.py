"""Nightly store backup (plan §A: backup/DR, restore tested, small but real).

Uses sqlite3's online backup API — safe while the ETL is running (no file copies of
half-flushed pages), and each target gets PRAGMA integrity_check afterwards, because
a backup that fails to open is a rumor, not a backup. Retention keeps the newest
N per database by mtime. Restore is a first-class command (the plan's "restore tested
monthly" is trivial if the command exists).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

KEEP_DEFAULT = 14


def _sources(paths: list[Path]) -> list[Path]:
    return [p for p in paths if Path(p).exists()]


def backup_all(dbs: list[Path], dest_dir: Path, stamp: str | None = None) -> dict:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    written, failed = [], []
    for db in _sources([Path(p) for p in dbs]):
        out = dest_dir / f"{db.stem}-{stamp}.sqlite"
        try:
            src = sqlite3.connect(str(db))
            dst = sqlite3.connect(str(out))
            with dst:
                src.backup(dst)
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            src.close()
            dst.close()
            if not ok:
                out.unlink(missing_ok=True)
                failed.append(f"{db.name}: integrity_check failed")
            else:
                written.append(out)
        except sqlite3.Error as e:
            out.unlink(missing_ok=True)
            failed.append(f"{db.name}: {type(e).__name__}: {e}")
    return {"stamp": stamp, "written": [str(w) for w in written], "failed": failed}


def prune(dest_dir: Path, keep: int = KEEP_DEFAULT) -> list[str]:
    """Keep newest `keep` per database stem; return deleted names."""
    removed = []
    groups: dict[str, list[Path]] = {}
    for f in sorted(Path(dest_dir).glob("*.sqlite")):
        # backup names are <db-stem>-YYYYMMDD-HHMMSS.sqlite — group by the DB,
        # not by day (rsplit 2 keeps multi-word stems intact)
        stem = f.name.rsplit("-", 2)[0] if f.name.count("-") >= 2 else f.stem
        groups.setdefault(stem, []).append(f)
    for files in groups.values():
        for old in sorted(files, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)[keep:]:
            old.unlink()
            removed.append(old.name)
    return removed


def restore(backup_path: Path, target: Path) -> str:
    """Copy a verified backup back over a target. The CURRENT target is kept beside
    it as <target>.pre-restore (one level deep is enough — the backups dir has the rest)."""
    src, dst = Path(backup_path), Path(target)
    if not src.exists():
        raise FileNotFoundError(f"no such backup: {src}")
    con = sqlite3.connect(str(src))
    ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()
    if not ok:
        raise sqlite3.DatabaseError(f"backup failed integrity_check, refusing to restore: {src}")
    if dst.exists():
        dst.rename(dst.with_suffix(dst.suffix + ".pre-restore"))
    con = sqlite3.connect(str(src))
    dst.parent.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(str(dst))
    with out:
        con.backup(out)
    con.close()
    out.close()
    return str(dst)
