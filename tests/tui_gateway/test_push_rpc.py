"""push.* / app.state RPCs and the reply-push hook in tui_gateway.server."""
import pytest

from gateway.push import apns
from tui_gateway import server


class _Sender:
    def __init__(self, tmp_path, avail=True):
        self.store = apns.TokenStore(tmp_path / "d.json")
        self.avail = avail

    def available(self):
        return self.avail


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    s = _Sender(tmp_path)
    monkeypatch.setattr(apns, "get_sender", lambda: s)
    apns.set_background(False)
    yield s
    apns.set_background(False)


def _call(method, params):
    return server._methods[method](1, params)


def test_register_validates_dedupes_unregisters(fake):
    assert "error" in _call("push.register", {"token": "nothex!"})
    tok = "AB" * 32
    assert _call("push.register", {"token": tok, "env": "production"})["result"]["registered"]
    _call("push.register", {"token": tok.lower(), "env": "development"})
    assert len(fake.store.all()) == 1
    assert _call("push.unregister", {"token": tok})["result"]["removed"] is True
    assert fake.store.all() == []


def test_app_state_toggles_background(fake):
    assert _call("app.state", {"background": True})["result"]["background"] is True
    assert apns.is_background() is True


def test_hook_fires_only_detached_or_background(fake, monkeypatch):
    sent = []
    monkeypatch.setattr(apns, "notify_turn_complete", lambda *a, **k: sent.append((a, k)) or True)
    live = object()  # not the detached sentinel, not closed
    session = {"transport": live, "session_key": "K1", "agent": None}
    monkeypatch.setattr(server, "_get_db", lambda: None)
    server._push_reply_notification("sid", session, "hi", "complete")
    assert sent == []
    session["transport"] = server._detached_ws_transport
    server._push_reply_notification("sid", session, "hi", "complete")
    assert len(sent) == 1 and sent[0][0][0] == "K1" and sent[0][1]["detached"] is True
    session["transport"] = live
    apns.set_background(True)
    server._push_reply_notification("sid", session, "hi", "complete")
    assert len(sent) == 2


def test_hook_noop_when_push_disabled(fake, monkeypatch):
    fake.avail = False
    monkeypatch.setattr(apns, "notify_turn_complete", lambda *a, **k: pytest.fail("called"))
    server._push_reply_notification(
        "sid", {"transport": server._detached_ws_transport}, "hi", "complete")
