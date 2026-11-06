"""Device authorization must be checked even if transport admission regresses."""
from types import SimpleNamespace


def test_device_cannot_read_another_profiles_live_session(tmp_path, monkeypatch):
    from clover_cli import mobile_pairing
    from tui_gateway import server

    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    device = store.create('phone')
    principal = store.authenticate(device['token'])
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setitem(server._sessions, 'foreign', {
        'profile_home': str(tmp_path / 'other-profile'),
        'history': [{'role': 'user', 'content': 'private'}],
    })
    transport = SimpleNamespace(auth_identity={
        'provider': 'mobile-device', 'device_principal': principal,
    })
    response = server.dispatch({'id': 1, 'method': 'session.history',
                                'params': {'session_id': 'foreign'}}, transport)
    assert response is not None
    assert response.get('error'), response
    assert 'private' not in str(response)


def test_device_admin_and_long_running_rpcs_are_denied_but_owner_works():
    from tui_gateway import server

    device = SimpleNamespace(auth_identity={'provider': 'mobile-device'})
    for method in ('config.set', 'prompt.submit', 'session.create'):
        response = server.dispatch({'id': 2, 'method': method, 'params': {}}, device)
        assert response is not None
        assert response['error']['code'] == 4030
    owner = SimpleNamespace(auth_identity={'provider': 'dashboard', 'user_id': 'owner'})
    response = server.dispatch({'id': 3, 'method': 'unknown-method', 'params': {}}, owner)
    assert response is not None
    assert response['error']['code'] == -32601
