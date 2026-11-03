"""A dropped WS must not kill a running turn (mobile cellular blips).

Detached-continue: the turn keeps running, events/final reply are buffered in
the replay ring, and the session is reaped only after the turn is done and
``_WS_DETACHED_TTL_S`` passes without a reconnect.
"""
import threading
import types

import pytest

from tui_gateway import event_replay, server


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(server, "_WS_ORPHAN_REAP_GRACE_S", 0.01)
    monkeypatch.setattr(server, "_WS_DETACHED_TTL_S", 1800.0)
    event_replay.reset_replay_state()
    yield
    event_replay.reset_replay_state()
    for sid in [s for s in server._sessions if s.startswith("dt-")]:
        server._sessions.pop(sid, None)
        server._cancel_ws_orphan_reap(sid)


class _Timer:
    created = []

    def __init__(self, delay, cb):
        self.delay, self.cb, self.daemon = delay, cb, False
        _Timer.created.append(self)

    def start(self):
        pass

    def cancel(self):
        pass


@pytest.fixture
def timers(monkeypatch):
    _Timer.created = []
    monkeypatch.setattr(server.threading, "Timer", _Timer)
    return _Timer.created


def _sess(agent=None, **extra):
    return {
        "agent": agent or types.SimpleNamespace(),
        "session_key": "k",
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "slash_worker": None,
        "transport": server._detached_ws_transport,
        **extra,
    }


def _fire(timers):
    timers.pop(0).cb()


def test_disconnect_mid_turn_does_not_interrupt(monkeypatch, timers):
    interrupts, torn = [], []
    monkeypatch.setattr(server, "_interrupt_session_turn", lambda *a, **k: interrupts.append(a))
    monkeypatch.setattr(server, "_teardown_popped_session", lambda s, **k: torn.append(s))
    server._sessions["dt-run"] = _sess(running=True)
    server._schedule_ws_orphan_reap("dt-run")
    for _ in range(5):  # well past the old 20s interrupt point, many polls
        _fire(timers)
        assert len(timers) == 1  # re-armed, never dropped
    assert interrupts == [] and torn == []
    assert "dt-run" in server._sessions


def test_turn_completes_then_reaps_only_after_ttl(monkeypatch, timers):
    torn = []
    monkeypatch.setattr(server, "_interrupt_session_turn", lambda *a, **k: pytest.fail("interrupted"))
    monkeypatch.setattr(server, "_teardown_popped_session", lambda s, **k: torn.append((s, k)))
    clock = [1000.0]
    monkeypatch.setattr(server.time, "time", lambda: clock[0])
    live = _sess(running=True)
    server._sessions["dt-ttl"] = live
    server._schedule_ws_orphan_reap("dt-ttl")
    _fire(timers)
    live["running"] = False  # turn finished while detached
    clock[0] += 10
    _fire(timers)
    assert torn == [] and "dt-ttl" in server._sessions  # parked, buffered
    clock[0] += 1799
    _fire(timers)
    assert torn == []  # idle 1809s measured from first idle poll (t=1010) -> 1799 < 1800
    clock[0] += 2
    _fire(timers)
    assert len(torn) == 1 and torn[0][1]["end_reason"] == "ws_orphan_reap"
    assert "dt-ttl" not in server._sessions


def test_ttl_zero_keeps_legacy_interrupt(monkeypatch, timers):
    monkeypatch.setattr(server, "_WS_DETACHED_TTL_S", 0.0)
    calls = []
    monkeypatch.setattr(server, "_interrupt_session_turn", lambda *a, **k: calls.append(a) or False)
    server._sessions["dt-legacy"] = _sess(running=True)
    server._schedule_ws_orphan_reap("dt-legacy")
    _fire(timers)
    assert len(calls) == 1


def test_reconnect_within_ttl_gets_final_reply_exactly_once():
    sid = "dt-replay"
    server._sessions[sid] = _sess(running=True)
    server._emit("message.delta", sid, {"text": "a"})  # watermark seen by client
    seen = event_replay.latest_seq(sid)
    # Phone drops; turn keeps emitting into the detached sink.
    server._emit("message.delta", sid, {"text": "b"})
    server._emit("message.complete", sid, {"text": "ab", "status": "complete"})
    first = event_replay.events_since(sid, seen)
    assert [e["type"] for e in first] == ["message.delta", "message.complete"]
    # Client applies it, advances watermark; a repeat resume yields nothing.
    new_mark = max(e["seq"] for e in first)
    assert event_replay.events_since(sid, new_mark) == []
    assert sum(e["type"] == "message.complete" for e in first) == 1


def test_reconnect_cancels_pending_reap_and_clears_idle(monkeypatch, timers):
    monkeypatch.setattr(server, "_interrupt_session_turn", lambda *a, **k: pytest.fail("interrupted"))
    live = _sess(running=False, _detached_idle_since=1.0)
    server._sessions["dt-back"] = live
    server._schedule_ws_orphan_reap("dt-back")
    class _T:
        def write(self, o):
            return True

    new_t = _T()
    server._live_session_payload("dt-back", live, transport=new_t, omit_messages=True)
    assert "dt-back" not in server._pending_ws_reaps
    assert "_detached_idle_since" not in live


def test_explicit_interrupt_still_interrupts_immediately(monkeypatch):
    agent = types.SimpleNamespace(interrupted=[])
    agent.interrupt = lambda *a, **k: agent.interrupted.append(a)
    server._sessions["dt-stop"] = _sess(agent=agent, running=True, transport=None)
    server._sessions["dt-stop"]["transport"] = server._stdio_transport
    resp = server.handle_request(
        {"id": "1", "method": "session.interrupt", "params": {"session_id": "dt-stop"}}
    )
    assert "error" not in resp, resp
    assert agent.interrupted, "explicit stop must interrupt right away (TTL does not gate it)"


def test_buffer_cap_holds_and_final_result_survives_eviction():
    sid = "dt-cap"
    server._sessions[sid] = _sess(running=True)
    server._emit("message.complete", sid, {"text": "FINAL"})
    for i in range(event_replay._REPLAY_BUFFER_MAX * 3):
        server._emit("message.delta", sid, {"text": str(i)})
    stats = event_replay.replay_stats()
    assert stats["events"] <= event_replay._REPLAY_BUFFER_MAX
    got = event_replay.events_since(sid, 0)
    finals = [e for e in got if e["type"] == "message.complete"]
    assert len(finals) == 1 and finals[0]["payload"]["text"] == "FINAL"
    assert [e["seq"] for e in got] == sorted(e["seq"] for e in got)


def test_detached_turn_past_max_is_interrupted_and_force_reaped(monkeypatch, timers, caplog):
    import logging

    monkeypatch.setattr(server, "_WS_DETACHED_TURN_MAX_S", 100.0)
    interrupts, torn = [], []
    monkeypatch.setattr(server, "_interrupt_session_turn", lambda *a, **k: interrupts.append(a) or False)
    monkeypatch.setattr(server, "_teardown_popped_session", lambda s, **k: torn.append(s))
    clock = [5000.0]
    monkeypatch.setattr(server.time, "time", lambda: clock[0])
    server._sessions["dt-max"] = _sess(running=True)
    server._schedule_ws_orphan_reap("dt-max")
    _fire(timers)
    assert interrupts == []
    clock[0] += 101
    with caplog.at_level(logging.ERROR, logger=server.logger.name):
        _fire(timers)
    assert len(interrupts) == 1
    assert any(r.levelno == logging.ERROR and "dt-max" in r.getMessage() for r in caplog.records)
    for _ in range(server._WS_ORPHAN_INTERRUPT_REAP_MAX_POLLS + 2):
        if torn or not timers:
            break
        _fire(timers)
    assert len(torn) == 1
    assert "dt-max" not in server._sessions


def test_session_idle_at_disconnect_is_popped_on_first_poll(monkeypatch, timers):
    torn = []
    monkeypatch.setattr(server, "_teardown_popped_session", lambda s, **k: torn.append(s))
    server._sessions["dt-idle"] = _sess(running=False)
    server._schedule_ws_orphan_reap("dt-idle")
    _fire(timers)
    assert len(torn) == 1 and "dt-idle" not in server._sessions


def test_session_that_ran_detached_turn_waits_for_ttl(monkeypatch, timers):
    torn = []
    monkeypatch.setattr(server, "_teardown_popped_session", lambda s, **k: torn.append(s))
    live = _sess(running=True)
    server._sessions["dt-ran"] = live
    server._schedule_ws_orphan_reap("dt-ran")
    _fire(timers)
    assert live["_detached_turn_seen"] is True
    live["running"] = False
    _fire(timers)
    assert torn == [] and "dt-ran" in server._sessions


def test_resume_fast_path_clears_detached_markers(monkeypatch):
    from tui_gateway import methods_session  # noqa: F401  (handlers installed)

    live = _sess(
        running=False,
        _detached_idle_since=1.0,
        _detached_since=1.0,
        _detached_turn_seen=True,
    )
    server._clear_detached_markers(live)
    assert not any(
        k in live for k in ("_detached_idle_since", "_detached_since", "_detached_turn_seen")
    )


def test_forget_drops_ring_and_pinned_final_on_reap(monkeypatch):
    sid = "dt-forget"
    server._emit("message.delta", sid, {"text": "a"})
    server._emit("message.complete", sid, {"text": "a", "status": "complete"})
    assert event_replay.events_since(sid, 0)
    sess = _sess()
    sess["_sid"] = sid
    monkeypatch.setattr(server, "_teardown_session", lambda *a, **k: None)
    assert server._teardown_popped_session(sess, end_reason="ws_orphan_reap")
    assert event_replay.events_since(sid, 0) == []
    assert sid not in event_replay._replay_final
    assert event_replay.latest_seq(sid) == 0
