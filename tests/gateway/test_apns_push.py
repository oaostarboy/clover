"""APNs reply push: JWT, payload, trigger gating, 410 cleanup. No network."""
import json
import threading

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from gateway.push import apns


@pytest.fixture()
def pem():
    k = ec.generate_private_key(ec.SECP256R1())
    return k, k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())


@pytest.fixture()
def sender(tmp_path, pem):
    key = tmp_path / "AuthKey.p8"
    key.write_bytes(pem[1])
    cfg = {"push": {"apns": {"enabled": True, "key_id": "KID123", "team_id": "TEAM123",
                             "key_path": str(key), "bundle_id": "ca.test.app"}}}
    calls = []

    def transport(url, headers, body):
        calls.append((url, headers, body))
        return 202, {}

    s = apns.ApnsSender(cfg, apns.TokenStore(tmp_path / "d.json"), transport)
    s.calls = calls
    return s


@pytest.fixture(autouse=True)
def _reset_bg():
    apns.set_background(False)
    yield
    apns.set_background(False)


def test_jwt_claims_and_signature(pem):
    tok = apns.build_jwt(pem[1], "KID123", "TEAM123", now=1000)
    assert jwt.get_unverified_header(tok)["kid"] == "KID123"
    claims = jwt.decode(tok, pem[0].public_key(), algorithms=["ES256"], options={"verify_iat": False})
    assert claims == {"iss": "TEAM123", "iat": 1000}


def test_jwt_cached_50min(pem):
    c = apns._JWTCache()
    a = c.get(pem[1], "K", "T", now=0)
    assert c.get(pem[1], "K", "T", now=2999) == a
    assert c.get(pem[1], "K", "T", now=3001) != a


def test_payload_shape_truncation_markdown():
    p = apns.build_payload("Greeting", "**Hi** Anthony! `code` [link](http://x)\n\n- item " + "x" * 400, "S1")
    assert p["session_id"] == "S1" and p["aps"]["thread-id"] == "S1"
    assert p["aps"]["alert"]["title"] == "Greeting"
    body = p["aps"]["alert"]["body"]
    assert body.startswith("Hi Anthony! code link item") and "*" not in body
    assert len(body) <= 180 and body.endswith("…")
    assert apns.build_payload("", "hi", "S")["aps"]["alert"]["title"] == "Clover"


def test_disabled_without_key(tmp_path):
    s = apns.ApnsSender({"push": {"apns": {"enabled": True, "key_id": "a", "team_id": "b",
                                           "key_path": str(tmp_path / "none.p8")}}},
                        apns.TokenStore(tmp_path / "d.json"), lambda *a: (202, {}))
    s.store.register("ab" * 32)
    assert not s.available() and s.send({}) == 0
    assert not apns.ApnsSender({}, apns.TokenStore(tmp_path / "e.json")).available()


def test_send_hosts_per_env_and_headers(sender):
    sender.store.register("aa" * 32, "production")
    sender.store.register("bb" * 32, "development")
    assert sender.send(apns.build_payload("t", "b", "S"), collapse_id="S") == 2
    urls = {c[0] for c in sender.calls}
    assert urls == {f"{apns.HOST_PRODUCTION}/3/device/{'aa' * 32}",
                    f"{apns.HOST_SANDBOX}/3/device/{'bb' * 32}"}
    h = sender.calls[0][1]
    assert h["apns-topic"] == "ca.test.app" and h["authorization"].startswith("bearer ")
    assert h["apns-push-type"] == "alert"


def test_dedupe_and_mode_600(sender):
    sender.store.register("aa" * 32)
    sender.store.register("aa" * 32, "development")
    rows = sender.store.all()
    assert len(rows) == 1 and rows[0]["env"] == "development"
    assert oct(sender.store.path.stat().st_mode & 0o777) == "0o600"


def test_410_and_baddevicetoken_remove(sender):
    for t in ("aa", "bb", "cc"):
        sender.store.register(t * 32)
    resp = {"aa" * 32: (410, {"reason": "Unregistered"}),
            "bb" * 32: (400, {"reason": "BadDeviceToken"}),
            "cc" * 32: (202, {})}
    sender._transport = lambda url, h, b: resp[url.rsplit("/", 1)[1]]
    assert sender.send({}) == 1
    assert [r["token"] for r in sender.store.all()] == ["cc" * 32]


class _SyncThread(threading.Thread):
    def start(self):
        self.run()


def _run(sender, monkeypatch, **kw):
    monkeypatch.setattr(apns.threading, "Thread", _SyncThread)
    return apns.notify_turn_complete("S", "Title", "Hello there", sender=sender, **kw)


def test_trigger_only_when_detached_or_background(sender, monkeypatch):
    sender.store.register("aa" * 32)
    assert _run(sender, monkeypatch, status="complete", detached=False) is False
    assert not sender.calls
    assert _run(sender, monkeypatch, status="complete", detached=True) is True
    assert len(sender.calls) == 1
    apns.set_background(True)
    assert _run(sender, monkeypatch, status="complete", detached=False) is True


def test_trigger_skips_interrupted_empty_and_formats_error(sender, monkeypatch):
    sender.store.register("aa" * 32)
    assert _run(sender, monkeypatch, status="interrupted", detached=True) is False
    assert apns.notify_turn_complete("S", "T", "  ", "complete", detached=True, sender=sender) is False
    _run(sender, monkeypatch, status="error", error="rate limited", detached=True)
    body = json.loads(sender.calls[-1][2])
    assert body["aps"]["alert"]["title"] == "Clover hit an error"
    assert "rate limited" in body["aps"]["alert"]["body"]


def test_trigger_noop_when_unavailable(tmp_path):
    s = apns.ApnsSender({}, apns.TokenStore(tmp_path / "d.json"))
    assert apns.notify_turn_complete("S", "T", "hi", "complete", detached=True, sender=s) is False
