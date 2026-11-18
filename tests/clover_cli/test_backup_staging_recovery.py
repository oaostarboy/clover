import os
import subprocess
import sys

from clover_cli import backup


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_dead_owner_staging_is_removed_published_kept(tmp_path):
    dead = _dead_pid()
    zip_partial = tmp_path / f".b.zip.{dead}-1.partial"
    zip_partial.write_bytes(b"half")
    db_partial = tmp_path / f".clover-backup-dbstage.{dead}.abc.partial"
    db_partial.write_bytes(b"half")
    snap_stage = tmp_path / f".20260101-000000.{dead}.partial"
    snap_stage.mkdir()
    (snap_stage / "state.db").write_bytes(b"x")
    published = tmp_path / "b.zip"
    published.write_bytes(b"ok")
    snap = tmp_path / "20260101-000000"
    snap.mkdir()
    other = tmp_path / ".unrelated.partial"
    other.write_bytes(b"?")

    removed = backup.recover_interrupted_backup_staging(tmp_path)

    assert len(removed) == 3
    assert not zip_partial.exists() and not db_partial.exists() and not snap_stage.exists()
    assert published.exists() and snap.exists() and other.exists()


def test_live_owner_staging_is_kept(tmp_path):
    mine = tmp_path / f".b.zip.{os.getpid()}-1.partial"
    mine.write_bytes(b"in progress")
    assert backup.recover_interrupted_backup_staging(tmp_path) == []
    assert mine.exists()


def test_snapshot_run_recovers_stale_staging(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("a: 1\n")
    root = backup._quick_snapshot_root(home)
    root.mkdir(parents=True)
    stale = root / f".old.{_dead_pid()}.partial"
    stale.mkdir()
    backup.create_quick_snapshot(clover_home=home)
    assert not stale.exists()
