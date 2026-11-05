"""Device resume must never adopt or read a foreign profile's stored session."""
from types import SimpleNamespace


def test_device_resume_does_not_adopt_foreign_stored_session(tmp_path, monkeypatch):
    from clover_cli import mobile_pairing, profiles
    from clover_state import SessionDB
    from tui_gateway import server

    default = tmp_path / 'default'
    other = tmp_path / 'other'
    default.mkdir()
    other.mkdir()
    donor = SessionDB(db_path=default / 'state.db')
    target = SessionDB(db_path=other / 'state.db')
    donor.create_session('foreign-session', source='tui')
    donor.append_message('foreign-session', 'user', 'foreign secret')
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'other')
    monkeypatch.setattr(profiles, 'get_profile_dir', lambda name: other if name == 'other' else default)
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    device = store.create('phone')
    transport = SimpleNamespace(auth_identity={'provider': 'mobile-device', 'device_principal': store.authenticate(device['token'])})
    monkeypatch.setattr(server, '_clover_home', str(default))
    monkeypatch.setattr(server, '_db', donor)
    binding = server.bind_transport(transport)
    try:
        result = server.handle_request({'id': 1, 'method': 'session.resume', 'params': {'session_id': 'foreign-session'}})
        assert result['error']['code'] == 4007
        result = server.handle_request({'id': 2, 'method': 'session.history', 'params': {'session_id': 'foreign-session'}})
        assert result['error']['code'] == 4030
        assert target.get_session('foreign-session') is None
        assert not donor.get_session('foreign-session')['archived']
        assert donor.get_messages('foreign-session')[0]['content'] == 'foreign secret'
    finally:
        server.reset_transport(binding)
        target.close()
        donor.close()
