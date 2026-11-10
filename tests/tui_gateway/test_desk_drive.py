"""desk.take_control / desk.input / desk.release over the phone's JSON-RPC channel."""
import time
from types import SimpleNamespace

import pytest

from clover_cli import desktop_control as dc
from clover_cli import mobile_pairing as mp

DISPLAY = {"id": 0, "name": "T", "x": 100, "y": 50, "width": 2000, "height": 1000,
           "stream_width": 1000, "stream_height": 500}


class FakeBackend(dc.Backend):
    name = "fake"

    def __init__(self):
        super().__init__()
        self.calls, self.notices = [], []

    def unavailable_reason(self):
        return None

    def displays(self):
        return [DISPLAY]

    def move(self, x, y): self.calls.append(("move", x, y))
    def click(self, x, y, b, dbl): self.calls.append(("click", x, y, b, dbl))
    def type_text(self, t): self.calls.append(("type", t))
    def key(self, n): self.calls.append(("key", n))
    def notify(self, m): self.notices.append(m)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class Tr:
    def __init__(self, identity):
        self.auth_identity = identity
        self.closed = False


@pytest.fixture
def env(tmp_path, monkeypatch):
    from clover_cli import profiles
    from tui_gateway import desk_drive
    clock = Clock()
    store = mp.DeviceStore(tmp_path / "devices.json", clock=clock)
    monkeypatch.setattr(mp, "get_device_store", lambda: store)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(profiles, "get_profile_dir", lambda name: tmp_path / name)
    (tmp_path / "default").mkdir()
    settings = {"enabled": True, "view_only": False, "allow_input": True}
    full = {**dc.read_settings({"desktop_control": settings})}
    cfg = {"v": full}
    monkeypatch.setattr(dc, "read_settings", lambda config=None: cfg["v"])
    backend = FakeBackend()
    ctl = dc.DesktopController(backend, full)
    dc.reset_controller_for_tests(ctl)
    desk_drive.reset_for_tests()
    monkeypatch.setattr(desk_drive, "_now", clock)
    yield SimpleNamespace(store=store, clock=clock, backend=backend, ctl=ctl, cfg=cfg, tmp=tmp_path)
    desk_drive.reset_for_tests()
    dc.reset_controller_for_tests(None)


def phone(env, caps):
    dev = env.store.create("phone")
    env.store.approve(dev["id"], caps)
    p = env.store.authenticate(dev["token"])
    return dev, Tr({"provider": "mobile-device", "device_principal": p})


def call(transport, method, params=None, rid=1):
    from tui_gateway import server
    from tui_gateway.transport import bind_transport, reset_transport
    tok = bind_transport(transport)
    try:
        return server.handle_request({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
    finally:
        reset_transport(tok)


MOVE = {"type": "move", "x": 500, "y": 250}


def test_device_without_control_drive_gets_4030_and_socket_stays_open(env):
    _, t = phone(env, ["chat", "control_view"])
    for m, p in (("desk.take_control", {"display": 0}), ("desk.input", {"events": [MOVE]}), ("desk.release", {})):
        r = call(t, m, p)
        assert r["error"]["code"] == 4030
        assert r["error"]["data"] == {"capability": "control_drive", "reason": "not_granted"}
    assert t.closed is False and env.backend.calls == [] and env.ctl.owner is None


def test_take_move_release_with_drive_and_pointer_maps_like_stream(env):
    _, t = phone(env, ["chat", "control_drive"])
    assert call(t, "desk.take_control", {"display": 0})["result"] == {"owner": "user"}
    r = call(t, "desk.input", {"events": [MOVE]})
    assert r["result"] == {"accepted": 1}
    assert env.backend.calls == [("move", *dc.map_point(DISPLAY, 500, 250))]
    assert call(t, "desk.release")["result"] == {"owner": None}
    assert env.ctl.owner is None
    assert call(t, "desk.input", {"events": [MOVE]})["error"]["code"] == 4040


def test_input_without_take_control_is_refused(env):
    _, t = phone(env, ["control_drive"])
    r = call(t, "desk.input", {"events": [MOVE]})
    assert r["error"]["code"] == 4040 and env.backend.calls == []


def test_disabled_by_config_even_with_grant(env):
    _, t = phone(env, ["control_drive"])
    env.cfg["v"] = {**env.cfg["v"], "allow_input": False}
    r = call(t, "desk.take_control", {"display": 0})
    assert r["error"]["code"] == 4030 and r["error"]["data"] == {"capability": "control_drive", "reason": "disabled"}
    env.cfg["v"] = {**env.cfg["v"], "allow_input": True, "view_only": True}
    assert call(t, "desk.take_control")["error"]["data"]["reason"] == "disabled"
    env.cfg["v"] = {**env.cfg["v"], "view_only": False, "enabled": False}
    assert call(t, "desk.take_control")["error"]["data"]["reason"] == "disabled"


def test_expiry_releases_control_immediately(env):
    _, t = phone(env, ["control_drive"])
    assert "result" in call(t, "desk.take_control")
    env.clock.t += 3601
    r = call(t, "desk.input", {"events": [MOVE]})
    assert r["error"]["code"] == 4030 and r["error"]["data"]["reason"] == "expired"
    assert env.ctl.owner is None and env.backend.calls == []


def test_expiry_sweep_frees_controller_without_phone_calls(env):
    from tui_gateway import desk_drive
    _, t = phone(env, ["control_drive"])
    call(t, "desk.take_control")
    env.clock.t += 3601
    desk_drive.sweep()
    assert env.ctl.owner is None


def test_revoke_via_owner_releases(env):
    from clover_cli import web_server
    dev, t = phone(env, ["control_drive"])
    call(t, "desk.take_control")
    env.store.set_capabilities(dev["id"], ["chat"])
    web_server._release_desk_for_device(dev["id"])
    assert env.ctl.owner is None
    r = call(t, "desk.input", {"events": [MOVE]})
    assert r["error"]["code"] == 4030


def test_event_limits(env):
    _, t = phone(env, ["control_drive"])
    call(t, "desk.take_control")
    assert call(t, "desk.input", {"events": [MOVE] * 60})["result"] == {"accepted": 60}
    r = call(t, "desk.input", {"events": [MOVE] * 61})
    assert r["error"]["code"] == 4002
    r = call(t, "desk.input", {"events": [{"type": "type", "text": "a" * 501}]})
    assert r["error"]["code"] == 4002
    assert call(t, "desk.input", {"events": [{"type": "type", "text": "a" * 500}]})["result"] == {"accepted": 1}
    assert call(t, "desk.input", {"events": []})["error"]["code"] == 4002
    assert call(t, "desk.input", {"events": [{"type": "rm"}]})["error"]["code"] == 4002
    assert call(t, "desk.input", {"events": [MOVE], "extra": 1})["error"]["code"] == 4030


def test_rate_limit_20_calls_per_second_then_recovers(env):
    _, t = phone(env, ["control_drive"])
    call(t, "desk.take_control")
    codes = [("error" in call(t, "desk.input", {"events": [MOVE]})) for _ in range(25)]
    assert codes == [False] * 20 + [True] * 5
    r = call(t, "desk.input", {"events": [MOVE]})
    assert r["error"]["code"] == 4290
    env.clock.t += 1.1
    assert call(t, "desk.input", {"events": [MOVE]})["result"] == {"accepted": 1}


def test_keys_text_click_scroll_reuse_stream_schema(env):
    _, t = phone(env, ["control_drive"])
    call(t, "desk.take_control")
    evs = [{"type": "click", "x": 10, "y": 10}, {"type": "type", "text": "hi"}, {"type": "key", "key": "enter"}]
    assert call(t, "desk.input", {"events": evs})["result"] == {"accepted": 3}
    assert [c[0] for c in env.backend.calls] == ["click", "type", "key"]
    assert env.backend.calls[2] == ("key", "Return")


def test_bad_event_reports_error_after_accepted_prefix(env):
    _, t = phone(env, ["control_drive"])
    call(t, "desk.take_control")
    r = call(t, "desk.input", {"events": [MOVE, {"type": "key", "key": "bogus-key-zzz"}, MOVE]})
    assert r["result"]["accepted"] == 1 and r["result"]["error"]["index"] == 1
    assert len(env.backend.calls) == 1


def test_second_phone_cannot_steal_control(env):
    _, a = phone(env, ["control_drive"])
    _, b = phone(env, ["control_drive"])
    assert "result" in call(a, "desk.take_control")
    r = call(b, "desk.take_control")
    assert r["error"]["code"] == 4091


def test_audit_log_lines(env, caplog):
    import logging
    _, t = phone(env, ["control_drive"])
    with caplog.at_level(logging.INFO, logger="clover_cli.desktop_control"):
        call(t, "desk.take_control")
        call(t, "desk.release")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "AUDIT take_control" in text and "AUDIT release" in text


def test_device_status_still_reports_drive_expiry(env):
    _, t = phone(env, ["control_drive"])
    r = call(t, "device.status")
    assert "control_drive" in r["result"]["expires"]


# ---- owner / direct path regressions ------------------------------------------------

def test_owner_transport_can_drive(env):
    t = Tr(None)
    assert call(t, "desk.take_control")["result"] == {"owner": "user"}
    assert call(t, "desk.input", {"events": [MOVE]})["result"] == {"accepted": 1}
    assert call(t, "desk.release")["result"] == {"owner": None}


def test_direct_stream_input_path_unchanged(env):
    import asyncio
    c = env.ctl
    client = object()
    assert c.take(client, "x") == (True, "")
    assert asyncio.run(c.handle_input(client, {"type": "move", "x": 1, "y": 1})) is None
    assert env.backend.calls[0][0] == "move"
    assert asyncio.run(c.handle_input(object(), MOVE)) == "not controlling; send take_control first"


# ---- the PC config flip is only safe if these hold ----------------------------------

def test_with_input_enabled_nothing_without_drive_can_reach_input(env):
    """allow_input true / view_only false: devices lacking control_drive and unpaired callers are refused."""
    for caps in (["chat"], ["chat", "control_view"], ["crew_read", "crew_act", "control_view"], []):
        _, t = phone(env, caps) if caps else (None, None)
        if t is None:
            dev = env.store.create("p", pending=True)
            t = Tr({"provider": "mobile-device", "device_principal": env.store.authenticate(dev["token"])})
        for m in ("desk.take_control", "desk.input", "desk.release"):
            assert call(t, m, {"events": [MOVE]} if m == "desk.input" else {})["error"]["code"] == 4030
    # a revoked / unknown device identity is refused too
    ghost = mp.DevicePrincipal("nope", "default", time.time() + 999, frozenset({"control_drive"}))
    r = call(Tr({"provider": "mobile-device", "device_principal": ghost}), "desk.take_control")
    assert r["error"]["code"] == 4030
    assert env.backend.calls == [] and env.ctl.owner is None
