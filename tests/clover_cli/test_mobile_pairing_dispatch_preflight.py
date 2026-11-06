from types import SimpleNamespace


def _transport(tmp_path, monkeypatch):
    from clover_cli import mobile_pairing, profiles
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'default')
    monkeypatch.setattr(profiles, 'get_profile_dir', lambda name: tmp_path / name)
    (tmp_path / 'default').mkdir()
    principal = store.authenticate(store.create('phone')['token'])
    return SimpleNamespace(auth_identity={'provider': 'mobile-device', 'device_principal': principal})


def test_device_dispatch_permits_scoped_create_and_rejects_admin(tmp_path, monkeypatch):
    from tui_gateway import server
    transport = _transport(tmp_path, monkeypatch)
    monkeypatch.setattr(server, 'handle_request', lambda request: {'id': request['id'], 'result': 'handled'})
    response = server.dispatch({'id': 1, 'method': 'session.create', 'params': {'source': 'mobile'}}, transport)
    assert response == {'id': 1, 'result': 'handled'}
    response = server.dispatch({'id': 2, 'method': 'config.set', 'params': {}}, transport)
    assert response['error']['code'] == 4030


def test_model_selection_cannot_persist_even_when_default_is_global(tmp_path, monkeypatch):
    from tui_gateway import server
    from clover_cli.model_switch import parse_model_switch_args, resolve_persist_behavior
    transport = _transport(tmp_path, monkeypatch)
    session = {'profile_home': str(tmp_path / 'default'), 'running': True}
    monkeypatch.setitem(server._sessions, 'own', session)
    token = server.bind_transport(transport)
    try:
        response = server.handle_request({'id': 9, 'method': 'config.set', 'params': {
            'session_id': 'own', 'key': 'model', 'value': 'example-model',
            'confirm_expensive_model': True,
        }})
    finally:
        server.reset_transport(token)
    assert response['result']['scope'] == 'session'
    parsed = parse_model_switch_args(session['pending_model_switch']['raw'])
    assert parsed.is_session
    assert not resolve_persist_behavior(parsed.is_global, parsed.is_session)


def test_session_resolution_rechecks_actual_profile(tmp_path, monkeypatch):
    from tui_gateway import server
    transport = _transport(tmp_path, monkeypatch)
    monkeypatch.setitem(server._sessions, 'foreign', {'profile_home': str(tmp_path / 'other')})
    token = server.bind_transport(transport)
    try:
        session, error = server._sess_nowait({'session_id': 'foreign'}, 1)
    finally:
        server.reset_transport(token)
    assert session is None
    assert error['error']['code'] == 4030
