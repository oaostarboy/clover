"""Security acceptance test: paired phone credentials are not admin keys."""

from aiohttp.test_utils import make_mocked_request


def test_device_token_cannot_authorize_arbitrary_delete(tmp_path, monkeypatch):
    from clover_cli.mobile_pairing import DeviceStore
    import clover_cli.mobile_pairing as pairing
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    store = DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(pairing, "get_device_store", lambda: store)
    token = store.create()["token"]
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "owner-test-key"}))
    request = make_mocked_request("DELETE", "/v1/admin/arbitrary", headers={"Authorization": f"Bearer {token}"})
    response = adapter._check_auth(request)
    assert response is not None and response.status in (401, 403)


def _repeated_verify(path, token, start):
    from clover_cli.mobile_pairing import DeviceStore

    store = DeviceStore(path)
    start.wait()
    for _ in range(50):
        store.verify(token)


def test_cross_process_revoke_does_not_resurrect(tmp_path):
    import multiprocessing
    from clover_cli.mobile_pairing import DeviceStore

    path = tmp_path / "devices.json"
    store = DeviceStore(path)
    device = store.create()
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    workers = [context.Process(target=_repeated_verify, args=(path, device["token"], start)) for _ in range(3)]
    for worker in workers:
        worker.start()
    start.set()
    assert store.revoke(device["id"])
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    assert not store.verify(device["token"])
    assert store.list() == []


def _repeated_create(path, start, created):
    from clover_cli.mobile_pairing import DeviceStore

    store = DeviceStore(path)
    start.wait()
    for _ in range(10):
        created.put(store.create()["id"])


def test_concurrent_creates_and_revoke_do_not_lose_or_restore_records(tmp_path):
    import multiprocessing
    from clover_cli.mobile_pairing import DeviceStore

    path = tmp_path / "devices.json"
    store = DeviceStore(path)
    old = store.create()
    context = multiprocessing.get_context("spawn")
    start, created = context.Event(), context.Queue()
    workers = [context.Process(target=_repeated_create, args=(path, start, created)) for _ in range(3)]
    for worker in workers:
        worker.start()
    start.set()
    assert store.revoke(old["id"])
    ids = [created.get(timeout=20) for _ in range(30)]
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    assert {entry["id"] for entry in store.list()} == set(ids)
    assert not store.verify(old["token"])


def test_active_device_expires(tmp_path):
    from clover_cli.mobile_pairing import DeviceStore, ACTIVE_TOKEN_TTL_SECONDS

    now = [1000.0]
    store = DeviceStore(tmp_path / "devices.json", clock=lambda: now[0])
    token = store.create()["token"]
    assert store.verify(token)
    now[0] += ACTIVE_TOKEN_TTL_SECONDS + 1
    assert not store.verify(token)


def test_device_history_routes_and_owner_key(tmp_path, monkeypatch):
    from clover_cli.mobile_pairing import DeviceStore
    import clover_cli.mobile_pairing as pairing
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    store = DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(pairing, "get_device_store", lambda: store)
    token = store.create()["token"]
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "owner-test-key"}))
    for method, path, allowed in [
        ("GET", "/api/sessions", True),
        ("GET", "/api/sessions/a/messages", True),
        ("DELETE", "/api/sessions/a/messages", False),
        ("GET", "/api/sessions/a/messages/other", False),
        ("GET", "/api/mobile/devices", False),
    ]:
        req = make_mocked_request(method, path, headers={"Authorization": f"Bearer {token}"})
        assert (adapter._check_auth(req) is None) is allowed
    req = make_mocked_request("DELETE", "/v1/admin/arbitrary", headers={"Authorization": "Bearer owner-test-key"})
    assert adapter._check_auth(req) is None


def test_device_cannot_authenticate_another_profile(tmp_path, monkeypatch):
    from clover_cli.mobile_pairing import DeviceStore
    import clover_cli.mobile_pairing as pairing
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter, _api_request_profile

    store = DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(pairing, "get_device_store", lambda: store)
    token = store.create()["token"]
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "owner-test-key"}))
    monkeypatch.setattr(adapter, "_expected_api_key", lambda: "profile-owner-key")
    scope = _api_request_profile.set("another-profile")
    try:
        req = make_mocked_request("GET", "/api/sessions", headers={"Authorization": f"Bearer {token}"})
        assert adapter._check_auth(req).status == 401
    finally:
        _api_request_profile.reset(scope)
