"""Durable receipts are written off the event loop, coalesced, and survive restart."""

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from plugins.platforms.telegram import update_admission as ua


def _adapter(tmp_path, n=0):
    seen = {f"7:{i}": time.time() for i in range(n)}
    return SimpleNamespace(
        _durable_update_receipts=True,
        _update_receipt_dir=Path(tmp_path),
        _seen_update_ids=seen,
        _inflight_update_ids={},
        _inflight_with_event=set(),
        _inflight_failed=set(),
    )


def test_persist_does_not_write_on_calling_thread(tmp_path, monkeypatch):
    threads = []
    real = ua.atomic_json_write

    def spy(path, data, **kw):
        threads.append(threading.current_thread())
        real(path, data, **kw)

    monkeypatch.setattr(ua, "atomic_json_write", spy)
    ad = _adapter(tmp_path, 5)
    ua._persist(ad, 7)
    assert ua.flush_receipts()
    assert threads and all(t is not threading.current_thread() for t in threads)


def test_burst_coalesces_and_ids_survive_restart(tmp_path, monkeypatch):
    gate, writes = threading.Event(), []
    real = ua.atomic_json_write

    def slow(path, data, **kw):
        gate.wait(5)
        writes.append(len(data["update_ids"]))
        real(path, data, **kw)

    monkeypatch.setattr(ua, "atomic_json_write", slow)
    ad = _adapter(tmp_path)
    for i in range(50):
        ad._seen_update_ids[f"7:{i}"] = time.time()
        ua._persist(ad, 7)
    gate.set()
    assert ua.flush_receipts()
    assert len(writes) < 50 and writes[-1] == 50
    path = tmp_path / "telegram_update_receipts_7.json"
    assert len(json.loads(path.read_text())["update_ids"]) == 50
    # Simulated restart: fresh map seeded from disk.
    fresh = {}
    ua.load_receipts(fresh, tmp_path, 7)
    assert set(fresh) == {f"7:{i}" for i in range(50)}


def test_load_sees_queued_write(tmp_path):
    ad = _adapter(tmp_path, 3)
    ua._persist(ad, 7)
    fresh = {}
    ua.load_receipts(fresh, tmp_path, 7)  # flushes first
    assert len(fresh) == 3
