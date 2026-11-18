import pytest
from tools.skills_hub import HubLockFile


def test_damaged_lock_raises_and_is_not_overwritten(tmp_path):
    p = tmp_path / "lock.json"
    p.write_text("{not json", encoding="utf-8")
    lock = HubLockFile(p)
    with pytest.raises(RuntimeError, match="lock"):
        lock.load()
    with pytest.raises(RuntimeError):
        lock.record_install("x", "s", "i", "t", "v", "h", "x", [])
    assert p.read_text(encoding="utf-8") == "{not json"


def test_wrong_shape_raises(tmp_path):
    p = tmp_path / "lock.json"
    p.write_text("[1]", encoding="utf-8")
    with pytest.raises(RuntimeError):
        HubLockFile(p).load()


def test_missing_lock_is_empty(tmp_path):
    assert HubLockFile(tmp_path / "nope.json").load()["installed"] == {}
