from types import SimpleNamespace

import pytest


def fixture(tmp_path, monkeypatch):
    from clover_cli import mobile_pairing, profiles
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'default')
    monkeypatch.setattr(profiles, 'get_profile_dir', lambda name: tmp_path / name)
    (tmp_path / 'default').mkdir()
    device = store.create('test phone')
    principal = store.authenticate(device['token'])
    transport = SimpleNamespace(auth_identity={'provider': 'mobile-device', 'device_principal': principal})
    return store, device, transport


def test_authorized_device_can_create_but_not_configure(tmp_path, monkeypatch):
    from tui_gateway.device_auth import authorize_request, DeviceAccessDenied
    _, _, transport = fixture(tmp_path, monkeypatch)
    params = authorize_request(transport, 'session.create', {'source': 'mobile'}, {})
    assert params['profile'] == 'default'
    with pytest.raises(DeviceAccessDenied):
        authorize_request(transport, 'config.set', {}, {})


def test_resolved_live_profile_not_request_hint_controls_access(tmp_path, monkeypatch):
    from tui_gateway.device_auth import authorize_request, DeviceAccessDenied
    _, _, transport = fixture(tmp_path, monkeypatch)
    sessions = {'foreign': {'profile_home': str(tmp_path / 'other')}}
    with pytest.raises(DeviceAccessDenied):
        authorize_request(transport, 'session.history', {'session_id': 'foreign'}, sessions,
                          launch_home=tmp_path / 'default')
    sessions['own'] = {'profile_home': str(tmp_path / 'default')}
    assert authorize_request(transport, 'session.history', {'session_id': 'own'}, sessions)['session_id'] == 'own'


def test_revoked_device_and_profile_override_rejected(tmp_path, monkeypatch):
    from tui_gateway.device_auth import authorize_request, DeviceAccessDenied
    store, device, transport = fixture(tmp_path, monkeypatch)
    with pytest.raises(DeviceAccessDenied):
        authorize_request(transport, 'session.create', {'profile': 'other'}, {})
    store.revoke(device['id'])
    with pytest.raises(DeviceAccessDenied):
        authorize_request(transport, 'session.create', {}, {})


def test_mobile_model_options_refresh_is_authorized(tmp_path, monkeypatch):
    from tui_gateway.device_auth import authorize_request
    _, _, transport = fixture(tmp_path, monkeypatch)
    params = authorize_request(transport, 'model.options', {'session_id': '', 'refresh': True}, {})
    assert params['refresh'] is True
    assert params['profile'] == 'default'


def test_mobile_uploaded_media_requires_owned_session_and_no_host_paths(tmp_path, monkeypatch):
    from tui_gateway.device_auth import authorize_request, DeviceAccessDenied
    _, _, transport = fixture(tmp_path, monkeypatch)
    sessions = {'own': {'profile_home': str(tmp_path / 'default')}}
    for method, params in [
        ('image.attach_bytes', {'session_id': 'own', 'content_base64': 'aGk=', 'filename': 'a.png'}),
        ('file.attach', {'session_id': 'own', 'data_url': 'data:text/plain;base64,aGk=', 'name': 'a.txt'}),
    ]:
        assert authorize_request(transport, method, params, sessions) == params
        with pytest.raises(DeviceAccessDenied):
            authorize_request(transport, method, {**params, 'path': '/etc/passwd'}, sessions)
        with pytest.raises(DeviceAccessDenied):
            authorize_request(transport, method, {**params, 'session_id': 'foreign'}, sessions)


def test_device_can_select_model_only_for_owned_live_session(tmp_path, monkeypatch):
    from tui_gateway.device_auth import authorize_request, DeviceAccessDenied
    _, _, transport = fixture(tmp_path, monkeypatch)
    sessions = {'own': {'profile_home': str(tmp_path / 'default')}}
    params = {'session_id': 'own', 'key': 'model', 'value': 'test', 'confirm_expensive_model': True}
    assert authorize_request(transport, 'config.set', params, sessions) == params
    for changed in ({'session_id': ''}, {'session_id': 'foreign'}, {'key': 'yolo'}, {'value': 'test --global'}):
        with pytest.raises(DeviceAccessDenied):
            authorize_request(transport, 'config.set', {**params, **changed}, sessions)


def test_owner_request_is_unchanged():
    from tui_gateway.device_auth import authorize_request
    params = {'key': 'value'}
    assert authorize_request(SimpleNamespace(auth_identity={'provider': 'owner'}), 'config.set', params, {}) is params
