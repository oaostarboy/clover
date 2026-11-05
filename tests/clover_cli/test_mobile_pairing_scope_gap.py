"""Regression for unintended owner-only session-list operations on device tokens."""

from aiohttp.test_utils import make_mocked_request


def test_device_history_rejects_principal_from_different_profile(tmp_path, monkeypatch):
    from clover_cli.mobile_pairing import DeviceStore
    import clover_cli.mobile_pairing as pairing
    import clover_cli.profiles as profiles
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    store = DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'other')
    token = store.create()['token']
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'default')
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'owner-test-key'}))
    request = make_mocked_request('GET', '/api/sessions', headers={'Authorization': f'Bearer {token}'})
    assert adapter._check_auth(request) is not None


def test_device_cannot_request_hidden_sessions(tmp_path, monkeypatch):
    from clover_cli.mobile_pairing import DeviceStore
    import clover_cli.mobile_pairing as pairing
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    store = DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(pairing, 'get_device_store', lambda: store)
    token = store.create()['token']
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'owner-test-key'}))
    request = make_mocked_request(
        'GET', '/api/sessions?title=Bot%20Chat&include_hidden=true',
        headers={'Authorization': f'Bearer {token}'},
    )
    response = adapter._check_auth(request)
    assert response is not None and response.status in (401, 403)
