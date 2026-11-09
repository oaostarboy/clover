"""Per-device grants: store, RPC enforcement, HTTP gate, owner endpoints, owner-path regression."""
import json
from types import SimpleNamespace

import pytest

from clover_cli import mobile_pairing as mp


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path, monkeypatch):
    from clover_cli import profiles
    clock = Clock()
    store = mp.DeviceStore(tmp_path / "devices.json", clock=clock)
    monkeypatch.setattr(mp, "get_device_store", lambda: store)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(profiles, "get_profile_dir", lambda name: tmp_path / name)
    for name in ("default", "octavia", "dev"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(profiles, "list_profiles", lambda: [SimpleNamespace(name=n) for n in ("default", "octavia", "dev")])
    return SimpleNamespace(store=store, clock=clock, tmp=tmp_path)


def transport_for(store, token):
    principal = store.authenticate(token)
    return SimpleNamespace(auth_identity={"provider": "mobile-device", "device_principal": principal})


def auth(transport, method, params=None, sessions=None):
    from tui_gateway.device_auth import authorize_request
    return authorize_request(transport, method, params or {}, sessions or {})


def denial(transport, method, params=None):
    from tui_gateway.device_auth import DeviceAccessDenied
    with pytest.raises(DeviceAccessDenied) as exc:
        auth(transport, method, params)
    return exc.value


BASE = sorted(mp.BASE_CAPABILITIES)


# ---- store -------------------------------------------------------------------

def test_relay_paired_device_starts_pending_with_no_caps(env):
    dev = env.store.create("phone", pending=True)
    p = env.store.authenticate(dev["token"])
    assert p.status == "pending" and p.capabilities == frozenset()
    assert env.store.get(dev["id"])["status"] == "pending"


def test_legacy_record_is_approved_with_exactly_its_caps(env):
    dev = env.store.create("old")
    raw = json.loads(env.store.path.read_text())
    for rec in raw["devices"]:
        rec.pop("status", None)
        rec["capabilities"] = ["chat", "history", "media", "model_selection"]
    env.store.path.write_text(json.dumps(raw))
    fresh = mp.DeviceStore(env.store.path, clock=env.clock)
    p = fresh.authenticate(dev["token"])
    assert p.status == "approved"
    assert p.capabilities == frozenset({"chat", "history", "media", "model_selection"})


def test_new_non_relay_pairing_keeps_base_caps_and_no_new_ones(env):
    p = env.store.authenticate(env.store.create("x")["token"])
    assert p.capabilities == mp.BASE_CAPABILITIES


def test_implied_caps_and_unknown_rejected(env):
    assert "control_view" in mp.normalize_capabilities(["control_drive"])
    assert "crew_read" in mp.normalize_capabilities(["crew_act"])
    with pytest.raises(ValueError):
        mp.normalize_capabilities(["root"])


def test_control_drive_expires_after_one_hour_view_does_not(env):
    dev = env.store.create("p", pending=True)
    env.store.approve(dev["id"], ["chat", "control_drive"])
    row = env.store.get(dev["id"])
    assert set(row["expires"]) == {"control_drive"}
    p = env.store.authenticate(dev["token"])
    assert {"control_drive", "control_view"} <= p.capabilities
    env.clock.t += 3601
    p2 = env.store.current_principal(p)
    assert "control_drive" not in p2.capabilities and "control_view" in p2.capabilities
    assert mp.denial_reason(p2, "control_drive")["reason"] == "expired"


def test_request_access_code_expiry_and_rate_limit(env):
    dev = env.store.create("p", pending=True)
    env.store.authenticate(dev["token"])  # first use: the unused-code TTL no longer applies
    req = env.store.request_access(dev["id"], ["crew_read"])
    assert len(req["match_code"]) == 4 and req["match_code"].isdigit()
    assert env.store.request_access(dev["id"], ["crew_read"])["request_id"] == req["request_id"]
    with pytest.raises(ValueError):
        env.store.request_access(dev["id"], ["control_view"])
    env.clock.t += 601
    assert "pending_request" not in env.store.get(dev["id"])


def test_approve_requires_matching_request_and_deny_removes_pending_device(env):
    dev = env.store.create("p", pending=True)
    req = env.store.request_access(dev["id"], ["crew_read"])
    with pytest.raises(ValueError):
        env.store.approve(dev["id"], ["chat"], request_id="nope")
    assert env.store.deny(dev["id"], req["request_id"])["removed"]
    assert env.store.get(dev["id"]) is None


def test_audit_is_append_only_and_has_no_secrets(env):
    dev = env.store.create("p", pending=True)
    env.store.approve(dev["id"], ["chat", "crew_read"])
    env.store.audit(dev["id"], "crew_read", "crew.list", "allow")
    text = env.store._audit_path().read_text()
    assert dev["token"] not in text and "cdt_" not in text
    tail = env.store.audit_tail(dev["id"])
    assert [e["method"] for e in tail] == ["owner.approve", "crew.list"]
    assert oct(env.store._audit_path().stat().st_mode & 0o777) == "0o600"


# ---- RPC enforcement ------------------------------------------------------------

def test_pending_device_gets_pending_reason_for_everything_but_status(env):
    dev = env.store.create("p", pending=True)
    t = transport_for(env.store, dev["token"])
    e = denial(t, "prompt.submit", {"session_id": "s", "text": "hi"})
    assert e.data == {"capability": "chat", "reason": "pending"}
    assert auth(t, "device.status") == {}
    assert auth(t, "device.request_access", {"capabilities": ["crew_read"]})


def test_crew_read_denied_then_allowed_on_next_call(env):
    dev = env.store.create("p")
    t = transport_for(env.store, dev["token"])
    for m in ("crew.list", "profiles.list"):
        assert denial(t, m).data == {"capability": "crew_read", "reason": "not_granted"}
    env.store.set_capabilities(dev["id"], BASE + ["crew_read"])
    assert auth(t, "crew.list") == {}
    assert auth(t, "session.list", {"profile": "octavia", "limit": 40}) == {"profile": "octavia", "limit": 40}
    env.store.set_capabilities(dev["id"], BASE)
    assert denial(t, "crew.list").data["reason"] == "not_granted"


def test_crew_read_does_not_allow_acting(env):
    dev = env.store.create("p")
    env.store.set_capabilities(dev["id"], BASE + ["crew_read"])
    t = transport_for(env.store, dev["token"])
    assert denial(t, "peer.turn", {"peer": "octavia", "text": "x"}).data["capability"] == "crew_act"
    assert denial(t, "session.create", {"profile": "octavia"}).data["capability"] == "crew_act"


def test_crew_act_allows_other_crew_profile_only(env):
    dev = env.store.create("p")
    env.store.set_capabilities(dev["id"], BASE + ["crew_act"])
    t = transport_for(env.store, dev["token"])
    assert auth(t, "session.create", {"profile": "octavia"})["profile"] == "octavia"
    assert auth(t, "peer.turn", {"peer": "dev", "text": "x"})
    denial(t, "session.create", {"profile": "not-a-crew-member"})
    denial(t, "peer.turn", {"peer": "dev", "text": "x", "evil": 1})


def test_profiles_configure_limited_to_crew_rooms(env):
    dev = env.store.create("p")
    env.store.set_capabilities(dev["id"], BASE + ["crew_act"])
    t = transport_for(env.store, dev["token"])
    assert auth(t, "profiles.configure", {"name": "default", "ui_meta": {"clover-bots-groups": {}}})
    denial(t, "profiles.configure", {"name": "default", "model": "x"})
    denial(t, "profiles.configure", {"name": "octavia", "ui_meta": {}})


def test_owner_ok_rejects_device_without_crew_act(env, monkeypatch):
    from tui_gateway import methods_crew, transport as tr
    dev = env.store.create("p")
    t = transport_for(env.store, dev["token"])
    monkeypatch.setattr(tr, "current_transport", lambda: t)
    assert methods_crew._owner_ok() is False
    env.store.set_capabilities(dev["id"], BASE + ["crew_act"])
    t2 = transport_for(env.store, dev["token"])
    monkeypatch.setattr(tr, "current_transport", lambda: t2)
    assert methods_crew._owner_ok() is True


def test_direct_owner_transport_is_unchanged(env):
    """Owner transports (no device identity) bypass device_auth entirely: params returned as-is."""
    from tui_gateway.device_auth import authorize_request
    for identity in (None, {"user_id": "ant", "provider": "basic"}):
        t = SimpleNamespace(auth_identity=identity)
        for method, params in (("peer.turn", {"peer": "x", "text": "y", "anything": 1}),
                               ("session.create", {"profile": "octavia"}),
                               ("config.set", {"key": "x"})):
            assert authorize_request(t, method, params, {}) is params


# ---- HTTP gate -------------------------------------------------------------------

def make_request(method, path, query=""):
    from starlette.requests import Request
    return Request({"type": "http", "method": method, "path": path, "raw_path": path.encode(),
                    "query_string": query.encode(), "headers": []})


def test_http_gate_codes(env, monkeypatch):
    from clover_cli import desktop_control
    monkeypatch.setattr(desktop_control, "read_settings", lambda config=None: {"enabled": True})
    dev = env.store.create("p")
    p = env.store.authenticate(dev["token"])
    gate = mp.device_http_gate
    snap = make_request("GET", "/desk/snapshot", "display=0")
    assert gate(snap, p) == {"error": "forbidden", "capability": "control_view", "reason": "not_granted"}
    env.store.set_capabilities(dev["id"], BASE + ["control_view"])
    p = env.store.authenticate(dev["token"])
    assert gate(snap, p) is None
    assert gate(make_request("GET", "/desk/displays"), p) is None
    assert gate(make_request("GET", "/desk/snapshot", "display=x"), p) == "unauthorized"
    assert gate(make_request("GET", "/desk/snapshot", "token=1"), p) == "unauthorized"
    assert gate(make_request("GET", "/desk/stream"), p) == "unauthorized"
    assert gate(make_request("GET", "/api/config"), p) == "unauthorized"
    assert gate(make_request("POST", "/desk/snapshot"), p) == "unauthorized"
    monkeypatch.setattr(desktop_control, "read_settings", lambda config=None: {"enabled": False})
    assert gate(snap, p)["reason"] == "disabled"


def test_pending_http_is_403_with_reason(env):
    dev = env.store.create("p", pending=True)
    p = env.store.authenticate(dev["token"])
    out = mp.device_http_gate(make_request("GET", "/api/sessions"), p)
    assert out["reason"] == "pending" and out["capability"] == "history"
    assert mp.device_http_gate(make_request("POST", "/api/auth/ws-ticket"), p) is None


# ---- owner endpoints over real ASGI ----------------------------------------------------

@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient
    from clover_cli import web_server
    c = TestClient(web_server.app)
    c.headers["X-Clover-Session-Token"] = web_server._SESSION_TOKEN
    return c


def test_owner_endpoints_approve_caps_deny_revoke(env, client):
    dev = env.store.create("My iPhone", pending=True)
    env.store.request_access(dev["id"], ["crew_read"])
    rows = client.get("/api/mobile/devices").json()["devices"]
    assert rows[0]["status"] == "pending" and rows[0]["pending_request"]["match_code"]
    blob = json.dumps(rows)
    assert "token" not in blob and "token_hash" not in blob
    rid = rows[0]["pending_request"]["request_id"]
    r = client.post(f"/api/mobile/devices/{dev['id']}/approve", json={"capabilities": ["chat", "crew_read"], "request_id": rid})
    assert r.status_code == 200 and r.json()["device"]["status"] == "approved"
    r = client.post(f"/api/mobile/devices/{dev['id']}/capabilities", json={"capabilities": ["chat", "control_drive"]})
    assert set(r.json()["device"]["capabilities"]) == {"chat", "control_drive", "control_view"}
    assert client.post(f"/api/mobile/devices/{dev['id']}/capabilities", json={"capabilities": ["sudo"]}).status_code == 400
    assert client.post("/api/mobile/devices/nope/deny", json={}).status_code == 404
    assert client.delete(f"/api/mobile/devices/{dev['id']}").status_code == 200
    assert env.store.get(dev["id"]) is None


def test_phone_cannot_approve_itself(env):
    dev = env.store.create("p", pending=True)
    from fastapi.testclient import TestClient
    from clover_cli import web_server
    phone = TestClient(web_server.app)
    r = phone.post(f"/api/mobile/devices/{dev['id']}/approve", json={"capabilities": ["chat"]},
                   headers={"Authorization": f"Bearer {dev['token']}"})
    assert r.status_code in (401, 403)
    assert env.store.get(dev["id"])["status"] == "pending"


def test_valid_but_ungranted_device_gets_403_invalid_gets_401(env):
    from fastapi.testclient import TestClient
    from clover_cli import web_server
    dev = env.store.create("p")
    c = TestClient(web_server.app)
    r = c.get("/desk/snapshot", headers={"Authorization": f"Bearer {dev['token']}"})
    assert r.status_code == 403
    assert r.json()["capability"] == "control_view" and r.json()["reason"] == "not_granted"
    assert c.get("/desk/snapshot", headers={"Authorization": "Bearer cdt_invalidinvalidinvalidinvalid"}).status_code == 401


def test_notification_text_is_short_and_secret_free():
    from clover_cli.mobile_device_notify import request_text
    t = request_text("iPhone", ["crew_read", "control_view", "control_drive"], "4821")
    assert t == "iPhone wants Crew view, Control drive. Code 4821. Approve in Clover on your PC."


# ---- the real dispatcher (handle_request), not just authorize_request ------------------

def _dispatch(transport, method, params=None, rid=1):
    from tui_gateway import server
    from tui_gateway.transport import bind_transport, reset_transport
    tok = bind_transport(transport)
    try:
        return server.handle_request({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
    finally:
        reset_transport(tok)


class _Tr:
    def __init__(self, identity):
        self.auth_identity = identity
        self.closed = False


def test_dispatch_device_status_and_request_access_and_4030(env, monkeypatch):
    from clover_cli import mobile_device_notify
    sent = []
    monkeypatch.setattr(mobile_device_notify, "announce_request", lambda dev, req: sent.append((dev, req)))
    dev = env.store.create("My iPhone", pending=True)
    env.store.authenticate(dev["token"])
    t = _Tr(transport_for(env.store, dev["token"]).auth_identity)

    r = _dispatch(t, "device.status")
    assert r["result"] == {"status": "pending", "capabilities": [], "expires": {}}

    r = _dispatch(t, "device.request_access", {"capabilities": ["crew_read", "control_view"]})
    assert len(r["result"]["match_code"]) == 4 and r["result"]["request_id"]
    assert len(sent) == 1 and "token" not in json.dumps(sent[0][1])
    again = _dispatch(t, "device.request_access", {"capabilities": ["crew_read", "control_view"]})
    assert again["result"] == r["result"] and len(sent) == 1  # no second alert
    assert _dispatch(t, "device.status")["result"]["pending_request"]["match_code"] == r["result"]["match_code"]

    r = _dispatch(t, "crew.list")
    assert r["error"]["code"] == 4030 and r["error"]["data"] == {"capability": "crew_read", "reason": "pending"}
    bad = _dispatch(t, "device.request_access", {"capabilities": ["root"]})
    assert bad["error"]["code"] == 4002


def test_dispatch_owner_transport_cannot_call_device_rpcs_and_keeps_peer_turn(env):
    r = _dispatch(_Tr(None), "device.status")
    assert r["error"]["code"] == 4031


def test_dispatch_crew_list_runs_through_the_rebound_handler(env, monkeypatch):
    """crew.list used to NameError (crew_roster) once server.py rebound the handler's globals."""
    from tui_gateway import methods_crew
    monkeypatch.setattr(methods_crew, "crew_roster", lambda: [{"id": "octavia"}])
    r = _dispatch(_Tr(None), "crew.list")
    assert r["result"] == {"agents": [{"id": "octavia"}]}
    dev = env.store.create("p")
    env.store.set_capabilities(dev["id"], BASE + ["crew_read"])
    assert _dispatch(_Tr(transport_for(env.store, dev["token"]).auth_identity), "crew.list")["result"]["agents"]
