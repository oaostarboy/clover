import json
import os
import stat
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from clover_cli.mobile_pairing import (
    LAST_SEEN_THROTTLE_SECONDS,
    UNUSED_CODE_TTL_SECONDS,
    DeviceStore,
    create_pairing,
    pairing_status,
    strict_https_origin,
)

KEY = "fake-master-key-0123456789abcdef"
CFG = {"mobile": {"public_url": "https://gateway.example.com"}}


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return DeviceStore(tmp_path / "mobile_devices.json", clock=clock)


def test_https_origin_only():
    assert strict_https_origin("https://gateway.example.com/") == "https://gateway.example.com"
    for bad in ["http://gateway.example.com", "https://u:p@x.com", "https://x.com/a", "", None, "192.168.1.5"]:
        assert strict_https_origin(bad) is None


def test_not_set_up_states_carry_no_credential():
    assert pairing_status({}, KEY)["configured"] is False
    assert pairing_status(CFG, "")["reason"] == "no_gateway_key"
    status = pairing_status(CFG, KEY)
    assert status["configured"] and KEY not in json.dumps(status)


def test_qr_link_never_contains_master_key_and_token_works(store):
    res = create_pairing(CFG, KEY, store)
    q = parse_qs(urlparse(res["uri"]).query)
    assert q["origin"] == ["https://gateway.example.com"]
    assert q["token"][0] != KEY and KEY not in res["uri"]
    assert store.verify(q["token"][0]) is True
    assert store.verify(q["token"][0] + "x") is False
    assert store.verify(KEY) is False


def test_unconfigured_creates_no_device(store):
    assert create_pairing({}, KEY, store)["configured"] is False
    assert store.list() == []


def test_only_hashes_on_disk_with_private_perms(store):
    token = create_pairing(CFG, KEY, store)["token"]
    raw = store.path.read_text()
    assert token not in raw and "token_hash" in raw
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert "token_hash" not in json.dumps(store.list())


def test_revoked_token_rejected_and_unknown_id_reported(store):
    res = create_pairing(CFG, KEY, store)
    assert store.verify(res["token"])
    assert store.revoke(res["device"]["id"]) is True
    assert store.verify(res["token"]) is False
    assert store.revoke(res["device"]["id"]) is False
    assert store.list() == []


def test_unused_code_expires_after_ten_minutes(store, clock):
    res = create_pairing(CFG, KEY, store)
    clock.t += UNUSED_CODE_TTL_SECONDS + 1
    assert store.verify(res["token"]) is False
    assert store.list() == []


def test_used_token_outlives_the_pairing_window(store, clock):
    res = create_pairing(CFG, KEY, store)
    clock.t += 60
    assert store.verify(res["token"])
    clock.t += 30 * 24 * 3600
    assert store.verify(res["token"])
    assert store.list()[0]["connected"] is True


def test_last_seen_is_throttled(store, clock):
    res = create_pairing(CFG, KEY, store)
    store.verify(res["token"])
    first = store.list()[0]["last_seen_at"]
    clock.t += 10
    store.verify(res["token"])
    assert store.list()[0]["last_seen_at"] == first
    clock.t += LAST_SEEN_THROTTLE_SECONDS
    store.verify(res["token"])
    assert store.list()[0]["last_seen_at"] > first


def test_revocation_by_another_process_is_seen(store, tmp_path, clock):
    res = create_pairing(CFG, KEY, store)
    store.verify(res["token"])
    DeviceStore(store.path, clock=clock).revoke(res["device"]["id"])
    assert store.verify(res["token"]) is False


# --- gateway API: every authenticated route (JSON and SSE) goes through _check_auth ---


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path))
    import clover_cli.mobile_pairing as mp

    monkeypatch.setattr(mp, "_default_store", None)
    return tmp_path


async def _client(adapter):
    app = web.Application()
    app.router.add_get("/v1/models", adapter._handle_models)
    app.router.add_post("/api/sessions/{session_id}/chat/stream", adapter._handle_session_chat_stream)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_gateway_accepts_master_and_device_token_but_not_revoked(isolated_home):
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from clover_cli.mobile_pairing import get_device_store

    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": KEY}))
    res = create_pairing(CFG, KEY, get_device_store())
    client = await _client(adapter)
    try:
        h = lambda t: {"Authorization": f"Bearer {t}"}
        assert (await client.get("/v1/models", headers=h(KEY))).status == 200
        assert (await client.get("/v1/models", headers=h(res["token"]))).status == 200
        assert (await client.get("/v1/models", headers=h("nope"))).status == 401
        # SSE route rejects before streaming when the token is bad.
        assert (await client.post("/api/sessions/s1/chat/stream", headers=h("nope"), json={})).status == 401
        get_device_store().revoke(res["device"]["id"])
        assert (await client.get("/v1/models", headers=h(res["token"]))).status == 401
        assert (await client.get("/v1/models", headers=h(KEY))).status == 200
    finally:
        await client.close()
