"""Loop-liveness dump carries the event-loop thread's own stack (faulthandler caps at 100 threads,
newest first, dropping the loop thread). Adapted from upstream hermes-agent 70ea34a496 / c8008fb1b0."""
from __future__ import annotations

import asyncio
import io
import threading
from unittest.mock import patch

from gateway.shutdown_watchdog import get_shutdown_watchdog_dump_path, start_loop_liveness_watchdog


def _wedge(tmp_path, monkeypatch, secret=None):
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path))
    state = {"secret": secret}
    frozen, release, fired = (threading.Event() for _ in range(3))

    def wedged_loop_callback_marker():
        held_credential = state.get("secret")
        frozen.set()
        release.wait(timeout=20.0)
        del held_credential

    def drive():
        loop = asyncio.new_event_loop()
        state["loop"] = loop
        loop.call_soon(lambda: start_loop_liveness_watchdog(
            loop, probe_interval=0.05, probe_timeout=0.05, max_strikes=1))
        loop.call_soon(wedged_loop_callback_marker)
        loop.run_forever()
        loop.close()

    t = threading.Thread(target=drive, name="test-gateway-loop", daemon=True)
    stderr = io.StringIO()
    with patch("gateway.shutdown_watchdog.os._exit", side_effect=lambda code: fired.set()), \
            patch("gateway.lifecycle_ledger.mark_exited"), \
            patch("gateway.shutdown_watchdog.sys.stderr", stderr), \
            patch("gateway.shutdown_watchdog.faulthandler.dump_traceback",
                  side_effect=lambda file=None, all_threads=True: (file or stderr).write(
                      "Thread 0x1 (most recent call first):\n...\n")):
        t.start()
        assert frozen.wait(timeout=5.0)
        try:
            assert fired.wait(timeout=10.0), "watchdog never fired"
        finally:
            release.set()
            state["loop"].call_soon_threadsafe(state["loop"].stop)
            t.join(timeout=5.0)
    path = get_shutdown_watchdog_dump_path(tmp_path)
    return (path.read_text(encoding="utf-8-sig") if path.exists() else ""), stderr.getvalue()


def test_stderr_dump_carries_loop_thread_stack_before_faulthandler(tmp_path, monkeypatch):
    _, err = _wedge(tmp_path, monkeypatch)
    assert "wedged_loop_callback_marker" in err
    assert err.index("wedged_loop_callback_marker") < err.index("most recent call first")


def test_file_dump_leads_with_loop_thread(tmp_path, monkeypatch):
    dump, _ = _wedge(tmp_path, monkeypatch)
    assert "wedged_loop_callback_marker" in dump
    assert "test-gateway-loop" in dump


def test_loop_section_has_no_frame_locals(tmp_path, monkeypatch):
    secret = "-".join(["sk", "test", "SHOULD", "NOT", "APPEAR"])
    dump, err = _wedge(tmp_path, monkeypatch, secret=secret)
    assert "wedged_loop_callback_marker" in err
    assert secret not in dump and secret not in err
