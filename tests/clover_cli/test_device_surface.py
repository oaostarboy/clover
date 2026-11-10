"""Phone app surface: every RPC / HTTP path the iOS tabs call maps to an existing capability."""
import json
from types import SimpleNamespace

import pytest

from clover_cli import mobile_pairing as mp
from tui_gateway.device_auth import DeviceAccessDenied, authorize_request


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path, monkeypatch):
    from clover_cli import profiles
    store = mp.DeviceStore(tmp_path / "devices.json", clock=Clock())
    monkeypatch.setattr(mp, "get_device_store", lambda: store)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(profiles, "get_profile_dir", lambda name: tmp_path / name)
    for name in ("default", "octavia"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(profiles, "list_profiles", lambda: [SimpleNamespace(name=n) for n in ("default", "octavia")])
    return SimpleNamespace(store=store, tmp=tmp_path)


def device(env, caps):
    dev = env.store.create("phone")
    env.store.set_capabilities(dev["id"], caps)
    p = env.store.authenticate(dev["token"])
    return dev, SimpleNamespace(auth_identity={"provider": "mobile-device", "device_principal": p})


def ok(t, method, params=None, sessions=None):
    return authorize_request(t, method, params or {}, sessions or {})


def refused(t, method, params=None):
    with pytest.raises(DeviceAccessDenied) as e:
        authorize_request(t, method, params or {}, {})
    return e.value


# ---- 1. approving a request ADDS, never replaces -------------------------------------

def test_approve_request_unions_with_existing_rights(env):
    dev = env.store.create("p")
    env.store.set_capabilities(dev["id"], sorted(mp.BASE_CAPABILITIES) + ["crew_read"])
    req = env.store.request_access(dev["id"], ["control_view"])
    env.store.approve(dev["id"], ["control_view"], request_id=req["request_id"])
    row = env.store.get(dev["id"])
    assert {"crew_read", "control_view"} <= set(row["effective_capabilities"])
    assert set(mp.BASE_CAPABILITIES) <= set(row["effective_capabilities"])


def test_set_capabilities_still_replaces_and_can_remove(env):
    dev = env.store.create("p")
    env.store.set_capabilities(dev["id"], sorted(mp.BASE_CAPABILITIES) + ["crew_read"])
    env.store.set_capabilities(dev["id"], sorted(mp.BASE_CAPABILITIES))
    assert "crew_read" not in env.store.get(dev["id"])["effective_capabilities"]


def test_approve_of_new_pending_device_unions_with_nothing(env):
    dev = env.store.create("p", pending=True)
    env.store.approve(dev["id"], ["chat", "history"])
    assert set(env.store.get(dev["id"])["effective_capabilities"]) == {"chat", "history"}


def test_union_does_not_restart_a_running_drive_clock(env):
    clock = env.store._clock
    dev = env.store.create("p")
    env.store.approve(dev["id"], ["chat", "control_drive"])
    first = env.store.get(dev["id"])["expires"]["control_drive"]
    clock.t += 600
    env.store.approve(dev["id"], ["crew_read"])
    assert env.store.get(dev["id"])["expires"]["control_drive"] == first


# ---- 2. own-profile session.list / todos.list need no crew_read ------------------------

def test_own_session_list_needs_history_not_crew_read(env):
    _, t = device(env, sorted(mp.BASE_CAPABILITIES))
    assert ok(t, "session.list", {"limit": 40})["profile"] == "default"
    assert ok(t, "session.list", {"profile": "default", "limit": 40})["profile"] == "default"
    assert refused(t, "session.list", {"profile": "octavia", "limit": 40}).data["capability"] == "crew_read"


def test_own_session_list_denied_without_history(env):
    _, t = device(env, ["chat"])
    assert refused(t, "session.list", {"limit": 5}).data == {"capability": "history", "reason": "not_granted"}


def test_other_profile_session_list_with_crew_read(env):
    _, t = device(env, sorted(mp.BASE_CAPABILITIES) + ["crew_read"])
    assert ok(t, "session.list", {"profile": "octavia", "limit": 5})["profile"] == "octavia"


def test_todos_list_with_chat_or_history_only(env):
    _, t = device(env, ["chat"])
    ok(t, "todos.list")
    _, t2 = device(env, ["history"])
    ok(t2, "todos.list")
    _, t3 = device(env, ["audio_speak"])
    refused(t3, "todos.list")


# ---- 3. Creations: artifacts.list + file bytes + thumbs under media -------------------

def test_artifacts_list_under_media_own_profile_only(env):
    _, t = device(env, ["media"])
    assert ok(t, "artifacts.list", {"limit": 60, "kind": "images"})["limit"] == 60
    assert ok(t, "artifacts.list", {"limit": 60, "kind": "all", "refresh": True})
    refused(t, "artifacts.list", {"profile": "octavia"})
    refused(t, "artifacts.list", {"evil": 1})
    _, t2 = device(env, ["chat"])
    assert refused(t2, "artifacts.list", {}).data["capability"] == "media"


class Req:
    def __init__(self, method, path, query=None):
        self.method = method
        self.url = SimpleNamespace(path=path)
        self.query_params = query or {}
        self.scope = {"raw_path": path.encode()}


def register_artifact(env, monkeypatch, path):
    from tui_gateway import artifact_index
    aid = artifact_index.artifact_id(path)
    real = tmp = env.tmp / "pic.png"
    real.write_bytes(b"x")
    monkeypatch.setattr(artifact_index, "lookup", lambda home, i: str(real) if i == aid and path == str(tmp) else None)
    return aid


def test_http_download_and_thumb_need_media_and_a_listed_artifact(env, monkeypatch):
    dev, _ = device(env, ["media"])
    p = env.store.authenticate(env.store.create("x")["token"])  # unused principal sanity
    principal = env.store.current_principal(env.store.authenticate(
        [d for d in [env.store.create("y")]][0]["token"]))
    env.store.set_capabilities(principal.device_id, ["chat", "media"])
    principal = env.store.current_principal(principal)
    path = str(env.tmp / "pic.png")
    aid = register_artifact(env, monkeypatch, path)
    assert mp.device_http_gate(Req("GET", "/api/files/download", {"path": path}), principal) is None
    assert mp.device_http_gate(Req("GET", "/api/artifacts/thumb", {"id": aid}), principal) is None
    assert mp.device_http_gate(Req("GET", "/api/artifacts/thumb", {"id": aid, "size": "320"}), principal) is None
    # a path that Creations never listed is refused (no arbitrary file read)
    r = mp.device_http_gate(Req("GET", "/api/files/download", {"path": "/etc/passwd"}), principal)
    assert isinstance(r, dict) and r["capability"] == "media"
    assert isinstance(mp.device_http_gate(Req("GET", "/api/artifacts/thumb", {"id": "0" * 16}), principal), dict)
    assert mp.device_http_gate(Req("GET", "/api/files/download", {"path": path, "token": "x"}), principal) == "unauthorized"
    assert mp.device_http_gate(Req("POST", "/api/files/download", {"path": path}), principal) == "unauthorized"
    assert mp.device_http_gate(Req("GET", "/api/files/upload", {}), principal) == "unauthorized"


def test_http_download_denied_without_media(env, monkeypatch):
    d = env.store.create("z")
    env.store.set_capabilities(d["id"], ["chat"])
    principal = env.store.current_principal(env.store.authenticate(d["token"]))
    r = mp.device_http_gate(Req("GET", "/api/files/download", {"path": "/x.png"}), principal)
    assert r == {"error": "forbidden", "capability": "media", "reason": "not_granted"}


# ---- 4. the rest of the app surface ---------------------------------------------------------

CHAT_ONLY = [
    ("agents.list", {}), ("delegation.status", {}), ("delegation.status", {"recent_limit": 10}),
    ("cron.manage", {"action": "list", "include_disabled": True}),
    ("cron.manage", {"action": "pause", "name": "j1"}), ("cron.manage", {"action": "resume", "name": "j1"}),
    ("push.register", {"token": "ab" * 20, "env": "production", "device_name": "iPhone"}),
    ("push.unregister", {"token": "ab" * 20}), ("push.test", {}), ("app.state", {"background": True}),
    ("clarify.respond", {"request_id": "r", "answer": "a"}),
    ("subagent.interrupt", {"subagent_id": "s"}),
    ("todos.add", {"text": "buy milk"}),
    ("todos.update", {"id": "i", "done": True}), ("todos.update", {"id": "i", "owner": "you"}),
    ("todos.delete", {"id": "i"}), ("todos.reorder", {"ids": ["a", "b"]}),
]


@pytest.mark.parametrize("method,params", CHAT_ONLY)
def test_chat_covers_app_surface(env, method, params):
    _, t = device(env, ["chat"])
    assert ok(t, method, params) == params
    _, none = device(env, ["audio_speak"])
    assert refused(none, method, params).data["capability"] in ("chat", "history")


def test_cron_manage_only_list_pause_resume(env):
    _, t = device(env, ["chat"])
    for bad in ({"action": "create", "prompt": "x"}, {"action": "run", "name": "j"}, {"action": "remove", "name": "j"},
                {"action": "list", "profile": "octavia"}):
        refused(t, "cron.manage", bad)


def test_config_get_provider_only_under_model_selection(env):
    _, t = device(env, ["model_selection"])
    ok(t, "config.get", {"key": "provider"})
    refused(t, "config.get", {"key": "api_keys"})
    _, c = device(env, ["chat"])
    assert refused(c, "config.get", {"key": "provider"}).data["capability"] == "model_selection"


def test_assign_and_approve_stay_crew_act(env):
    _, t = device(env, ["chat", "crew_read"])
    assert refused(t, "todos.add", {"text": "x", "assignee": "octavia"}).data["capability"] == "crew_act"
    assert refused(t, "todos.update", {"id": "i", "assignee": "octavia"}).data["capability"] == "crew_act"
    assert refused(t, "todos.approve", {"id": "i"}).data["capability"] == "crew_act"
    _, a = device(env, ["chat", "crew_act"])
    ok(a, "todos.add", {"text": "x", "assignee": "octavia"})
    ok(a, "todos.update", {"id": "i", "assignee": "octavia"})
    ok(a, "todos.approve", {"id": "i"})


def test_todos_update_rejects_unknown_fields(env):
    _, t = device(env, ["chat"])
    refused(t, "todos.update", {"id": "i", "evil": 1})
    refused(t, "todos.add", {"text": "x", "evil": 1})


def test_session_bound_helpers_follow_session_ownership(env):
    _, t = device(env, ["chat", "history"])
    sessions = {"s1": {"profile_home": str(env.tmp / "default")}, "s2": {"profile_home": str(env.tmp / "octavia")}}
    for m, p in (("approval.pending", {}), ("subagent.steer", {"subagent_id": "s", "text": "go"}), ("approval.received", {"request_id": "r"}),
                 ("approval.respond", {"request_id": "r", "choice": "once", "all": False}),
                 ("session.events.since", {"last_seen": 3})):
        assert ok(t, m, {"session_id": "s1", **p}, sessions)
        with pytest.raises(DeviceAccessDenied):
            ok(t, m, {"session_id": "s2", **p}, sessions)
        refused(t, m, p)  # no session id at all


def test_never_for_devices(env):
    _, t = device(env, sorted(mp.BASE_CAPABILITIES) + ["crew_act", "control_drive"])
    for m in ("shell.exec", "cli.exec", "config.set", "skills.manage", "mcp.servers.add", "plugins.manage",
              "projects.list", "tools.configure", "session.delete", "terminal.resize", "process.kill"):
        if m == "config.set":
            continue  # model selection only; covered elsewhere
        refused(t, m, {})


def test_owner_transport_unchanged_for_all_new_rules(env):
    t = SimpleNamespace(auth_identity=None)
    for m, p in CHAT_ONLY + [("artifacts.list", {"profile": "octavia"}), ("config.get", {"key": "x"})]:
        assert authorize_request(t, m, p, {}) == p
