"""agents.list merge/dedupe/online cache + peer.turn relay (fake transport)."""

from __future__ import annotations

import urllib.error

import pytest

import tui_gateway.server as srv
from tui_gateway import crew
from tui_gateway.transport import bind_transport, reset_transport

PEERS = {
    "octavia": {"url": "http://127.0.0.1:8642"},
    "alfred": {"url": "http://127.0.0.1:18644"},
    "suni": {"url": "http://127.0.0.1:18645"},
    "vega": {"url": "http://127.0.0.1:8646"},
    "oracle": {"url": "http://127.0.0.1:18647"},
}
PROFILES = [
    {"name": "default", "is_default": True, "display_name": ""},
    {"name": "dev", "is_default": False, "display_name": ""},
    {"name": "octavia", "is_default": False, "display_name": "Octavia"},
]


@pytest.fixture(autouse=True)
def _clean():
    crew.clear_health_cache()
    yield
    crew.clear_health_cache()


def test_merge_dedupe_names_and_online():
    down = {"http://127.0.0.1:18644"}
    rows = crew.build_crew(PROFILES, PEERS, probe=lambda u: u not in down)
    by = {r["id"]: r for r in rows}
    assert by["default"]["display_name"] == "Clover" and by["default"]["kind"] == "profile"
    assert by["octavia"]["kind"] == "profile"  # profile path wins
    assert sum(1 for r in rows if r["display_name"] == "Octavia") == 1
    assert {k: by[k]["display_name"] for k in ("alfred", "suni", "oracle", "vega")} == {
        "alfred": "Alfred", "suni": "Suni", "oracle": "Oracle", "vega": "Vegas"}
    assert by["alfred"]["online"] is False and by["suni"]["online"] is True
    assert by["suni"]["kind"] == "peer" and by["suni"]["avatar_initial"] == "S"
    assert all("url" not in r and "key" not in r for r in rows)


def test_online_cached_30s_and_concurrent():
    calls = []
    t = [100.0]
    probe = lambda u: calls.append(u) or True  # noqa: E731
    crew.build_crew([], PEERS, probe=probe, now=lambda: t[0])
    assert len(calls) == 5
    t[0] += 10
    crew.build_crew([], PEERS, probe=probe, now=lambda: t[0])
    assert len(calls) == 5  # cached
    t[0] += 31
    crew.build_crew([], PEERS, probe=probe, now=lambda: t[0])
    assert len(calls) == 10


def test_probe_failure_is_offline_not_error():
    assert crew.probe_health("http://127.0.0.1:9", timeout=0.2) is False


class FakeT:
    def __init__(self, key="k", fail=None):
        self.key, self.fail, self.calls = key, fail, []

    def _parse_target(self, t):
        return t, None

    def _load_peers(self):
        return PEERS

    def _peer_secret(self, n):
        return self.key

    def _base_url(self, peer, profile):
        return peer["url"]

    def _ensure_bot_chat(self, base, key):
        return "botchat1"

    def _request(self, url, key, *, method="GET", body=None, timeout=30):
        self.calls.append((method, url, body, timeout))
        if self.fail and url.endswith("/chat"):
            raise self.fail
        if "/api/sessions?" in url:
            return {"data": []}
        if url.endswith("/api/sessions"):
            return {"session": {"id": "room-s1"}}
        return {"session_id": "room-s1", "message": {"content": "hi back"}}


def test_relay_creates_room_session_then_reuses_hint():
    t = FakeT()
    out = crew.relay_turn("suni", " hello ", room_id="r1", transport=t)
    assert out == {"peer": "suni", "session_id": "room-s1", "reply": "hi back"}
    create = [c for c in t.calls if c[0] == "POST" and c[1].endswith("/api/sessions")][0]
    assert create[2]["title"] == "Group: r1"
    chat = t.calls[-1]
    assert chat[1].endswith("/api/sessions/room-s1/chat") and chat[2] == {"message": "hello"}
    t2 = FakeT()
    crew.relay_turn("suni", "again", room_id="r1", session_hint="room-s1", transport=t2)
    assert len(t2.calls) == 1  # no lookup when hint given


def test_relay_without_room_uses_bot_chat():
    t = FakeT()
    out = crew.relay_turn("oracle", "x", transport=t)
    assert t.calls[-1][1].endswith("/api/sessions/botchat1/chat") and out["peer"] == "oracle"


@pytest.mark.parametrize("fail,code", [
    (TimeoutError("t"), 4106),
    (urllib.error.URLError(TimeoutError("t")), 4106),
    (urllib.error.URLError("refused"), 4107),
    (RuntimeError("bad"), 4108),
])
def test_relay_errors_surface(fail, code):
    with pytest.raises(crew.PeerRelayError) as e:
        crew.relay_turn("suni", "x", transport=FakeT(fail=fail))
    assert e.value.code == code


def test_relay_missing_key_unknown_peer_empty_text_and_no_key_leak():
    with pytest.raises(crew.PeerRelayError) as e:
        crew.relay_turn("suni", "x", transport=FakeT(key=""))
    assert e.value.code == 4103
    with pytest.raises(crew.PeerRelayError) as e:
        crew.relay_turn("nobody", "x", transport=FakeT())
    assert e.value.code == 4102
    with pytest.raises(crew.PeerRelayError):
        crew.relay_turn("suni", "  ", transport=FakeT())
    http = urllib.error.HTTPError("http://x", 401, "no", {}, None)
    with pytest.raises(crew.PeerRelayError) as e:
        crew.relay_turn("suni", "x", transport=FakeT(key="SECRETKEY", fail=http))
    assert "SECRETKEY" not in e.value.message


class _Tr:
    def __init__(self, identity):
        self.auth_identity = identity
        self.closed = False


def _call(identity, params, bind=True):
    tok = bind_transport(_Tr(identity) if bind else None)
    try:
        return srv._methods["peer.turn"](1, params)
    finally:
        reset_transport(tok)


def test_peer_turn_auth_rejection():
    from tui_gateway import methods_browser_control as bc

    p = {"peer": "suni", "text": "x"}
    assert _call(None, p, bind=False)["error"]["code"] == 4403
    r = _call({"user_id": bc._INTERNAL_USER_ID, "provider": bc._INTERNAL_PROVIDER}, p)
    assert r["error"]["code"] == 4403
    assert _call({"user_id": "", "provider": "x"}, p)["error"]["code"] == 4403


def test_peer_turn_rpc_ok_and_error(monkeypatch):
    ident = {"user_id": "owner", "provider": "pw"}
    monkeypatch.setattr(crew, "relay_turn", lambda *a, **k: {"peer": "suni", "session_id": "s", "reply": "r"})
    assert _call(ident, {"peer": "suni", "text": "x"})["result"]["reply"] == "r"

    def boom(*a, **k):
        raise crew.PeerRelayError(4106, "peer 'suni' didn't respond in time")

    monkeypatch.setattr(crew, "relay_turn", boom)
    assert _call(ident, {"peer": "suni", "text": "x"})["error"]["code"] == 4106


def test_agents_list_rpc_registered_long():
    assert "agents.list" in srv._methods and "peer.turn" in srv._LONG_HANDLERS
