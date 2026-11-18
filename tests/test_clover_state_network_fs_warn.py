import logging
import sqlite3

import clover_state as cs

MI = (
    "20 1 8:1 / / rw - ext4 /dev/sda1 rw\n"
    "30 20 0:40 / /mnt/nas rw - nfs4 srv:/x rw\n"
    "31 20 0:41 / /mnt/ssh rw - fuse.sshfs u@h:/ rw\n"
    "32 20 0:42 / /mnt/fuse rw - fuse.ntfs3g /dev/x rw\n"
)


def _mi(tmp_path):
    p = tmp_path / "mountinfo"
    p.write_text(MI)
    return str(p)


def test_fstype_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(cs.sys, "platform", "linux")
    mi = _mi(tmp_path)
    assert cs._path_on_network_fs("/mnt/nas/.clover/state.db", mi)
    assert cs._path_on_network_fs("/mnt/ssh/state.db", mi)
    assert not cs._path_on_network_fs("/mnt/fuse/state.db", mi)
    assert not cs._path_on_network_fs("/home/u/state.db", mi)


def test_warns_once_per_path(monkeypatch, caplog):
    cs._network_fs_warned_paths.clear()
    monkeypatch.setattr(cs, "_path_on_network_fs", lambda p, *a: True)
    with caplog.at_level(logging.WARNING, logger=cs.logger.name):
        assert cs._warn_if_network_fs("/mnt/nas/state.db")
        assert cs._warn_if_network_fs("/mnt/nas/state.db")
    assert len([r for r in caplog.records if "network filesystem" in r.getMessage()]) == 1


def test_apply_wal_warns_on_network_fs(tmp_path, monkeypatch, caplog):
    cs._network_fs_warned_paths.clear()
    monkeypatch.setattr(cs, "_path_on_network_fs", lambda p, *a: True)
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    with caplog.at_level(logging.WARNING, logger=cs.logger.name):
        cs.apply_wal_with_fallback(conn, db_label="s.db")
    assert any("network filesystem" in r.getMessage() for r in caplog.records)
