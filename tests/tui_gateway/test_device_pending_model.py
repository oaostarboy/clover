"""A paired phone on a fresh chat (no session yet) can pick a model: kept per device,
never written to global config, used by that device's next session.create."""
from types import SimpleNamespace

import pytest

from clover_cli import mobile_pairing as mp


class Tr:
    def __init__(self, identity):
        self.auth_identity = identity
        self.closed = False


@pytest.fixture
def env(tmp_path, monkeypatch):
    from clover_cli import profiles
    from tui_gateway import device_auth, server
    store = mp.DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(mp, "get_device_store", lambda: store)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(profiles, "get_profile_dir", lambda name: tmp_path / name)
    (tmp_path / "default").mkdir()
    device_auth.PENDING_MODELS.clear()
    written = []
    monkeypatch.setattr(server, "_persist_model_switch", lambda r: written.append(r))
    monkeypatch.setattr(server, "_write_config_key", lambda *a, **k: written.append(a))
    monkeypatch.setattr(server, "_schedule_agent_build", lambda sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)

    def fake_switch(sid, session, raw, **kw):
        assert kw.get("persist_override") is False or not kw.get("persist_override")
        session["model_override"] = {"model": raw, "provider": "prov"}
        return {"value": raw, "warning": "", "confirm_required": False, "scope": "session"}

    monkeypatch.setattr(server, "_apply_model_switch", fake_switch)
    yield SimpleNamespace(store=store, written=written)
    device_auth.PENDING_MODELS.clear()
    for k in list(server._sessions):
        server._sessions.pop(k, None)


def phone(env, caps):
    dev = env.store.create("phone")
    env.store.approve(dev["id"], caps)
    return dev, Tr({"provider": "mobile-device", "device_principal": env.store.authenticate(dev["token"])})


def call(t, method, params):
    from tui_gateway import server
    from tui_gateway.transport import bind_transport, reset_transport
    tok = bind_transport(t)
    try:
        return server.handle_request({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    finally:
        reset_transport(tok)


def test_device_config_set_model_without_session_is_ok_and_not_global(env):
    _, t = phone(env, ["chat", "model_selection"])
    r = call(t, "config.set", {"key": "model", "value": "vendor/m1", "confirm_expensive_model": True})
    assert r["result"]["value"] == "vendor/m1" and r["result"]["key"] == "model"
    assert set(r["result"]) >= {"key", "value", "warning", "confirm_required", "confirm_message", "scope"}
    assert env.written == []


def test_next_session_create_uses_pending_model_once_and_only_for_that_device(env):
    from tui_gateway import server
    _, t = phone(env, ["chat", "model_selection"])
    _, other = phone(env, ["chat", "model_selection"])
    call(t, "config.set", {"key": "model", "value": "vendor/m1"})
    r = call(other, "session.create", {})
    assert r["result"]["info"]["model"] != "vendor/m1"
    r = call(t, "session.create", {})
    assert r["result"]["info"]["model"] == "vendor/m1"
    assert server._sessions[r["result"]["session_id"]]["model_override"]["model"] == "vendor/m1"
    r2 = call(t, "session.create", {})
    assert r2["result"]["info"]["model"] != "vendor/m1"


def test_explicit_model_on_create_wins(env):
    _, t = phone(env, ["chat", "model_selection"])
    call(t, "config.set", {"key": "model", "value": "vendor/m1"})
    r = call(t, "session.create", {"model": "vendor/m2"})
    assert r["result"]["info"]["model"] == "vendor/m2"


def test_device_without_model_selection_gets_4030(env):
    dev, t = phone(env, ["chat", "model_selection"])
    env.store.set_capabilities(dev["id"], ["chat"])
    r = call(t, "config.set", {"key": "model", "value": "vendor/m1"})
    assert r["error"]["code"] == 4030
    assert r["error"]["data"]["capability"] == "model_selection"


def test_other_keys_and_bad_values_still_refused(env):
    _, t = phone(env, ["chat", "model_selection"])
    assert call(t, "config.set", {"key": "fast", "value": "on"})["error"]["code"] == 4030
    assert call(t, "config.set", {"key": "model", "value": "a b"})["error"]["code"] == 4030
    assert call(t, "config.set", {"key": "model", "value": "--global"})["error"]["code"] == 4030
    assert call(t, "config.set", {"key": "model", "value": "x", "session_id": "nope"})["error"]["code"] == 4030


def test_owner_path_unchanged_still_global_capable(env, monkeypatch):
    from tui_gateway import server
    seen = {}

    def fake(sid, session, raw, **kw):
        seen.update(raw=raw, kw=kw)
        return {"value": raw, "warning": "", "confirm_required": False, "scope": "global"}

    monkeypatch.setattr(server, "_apply_model_switch", fake)
    r = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "config.set",
                               "params": {"key": "model", "value": "vendor/m1"}})
    assert r["result"]["scope"] == "global" and seen["raw"] == "vendor/m1"
    assert "persist_override" not in seen["kw"]
