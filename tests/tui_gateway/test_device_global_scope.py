from tui_gateway import server


def test_foreign_reclaim_is_not_delivered_to_device(tmp_path, monkeypatch):
    from clover_cli import mobile_pairing, profiles
    home = tmp_path / 'home'; home.mkdir()
    foreign = tmp_path / 'foreign'; foreign.mkdir()
    monkeypatch.setattr(profiles, 'get_profile_dir', lambda name: home)
    monkeypatch.setattr(server, '_clover_home', home)
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    device = store.create()
    principal = store.authenticate(device['token'])
    class Transport:
        auth_identity = {'provider': 'mobile-device', 'device_principal': principal}
        def __init__(self): self.frames = []
        def write(self, frame): self.frames.append(frame)
    transport = Transport()
    server.register_live_transport(transport)
    try:
        server._announce_session_reclaimed({'_sid': 'foreign', 'session_key': 'stored', 'profile_home': str(foreign)}, 'idle_timeout')
        assert transport.frames == []
    finally:
        server.unregister_live_transport(transport)


def test_scope_and_owner_delivery(tmp_path, monkeypatch):
    from clover_cli import mobile_pairing, profiles
    home = tmp_path / 'home'; home.mkdir()
    monkeypatch.setattr(profiles, 'get_profile_dir', lambda name: home)
    monkeypatch.setattr(server, '_clover_home', home)
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    device = store.create()
    class Transport:
        def __init__(self, identity): self.auth_identity, self.frames = identity, []
        def write(self, frame): self.frames.append(frame)
    phone = Transport({'provider': 'mobile-device', 'device_principal': store.authenticate(device['token'])})
    owner = Transport(None)
    server.register_live_transport(phone); server.register_live_transport(owner)
    try:
        for scope in (None, {}, {'profile_home': 42}):
            server._broadcast_global_event('session.reclaimed', {}, source_session=scope)
        server._broadcast_global_event('unknown.sensitive', {'id': 'private'})
        assert phone.frames == []
        assert len(owner.frames) == 4
        session = {'_sid': 'live', 'session_key': 'stored', 'profile_home': str(home)}
        server._announce_session_reclaimed(session, 'idle_timeout')
        assert phone.frames[-1]['params']['payload'] == {'session_id': 'live', 'stored_session_id': 'stored', 'reason': 'idle_timeout'}
        assert len(owner.frames) == 5
        server._announce_session_reclaimed({'_sid': 'launch', 'profile_home': None}, 'idle_timeout')
        assert phone.frames[-1]['params']['payload']['session_id'] == 'launch'
        store.revoke(device['id'])
        server._announce_session_reclaimed(session, 'idle_timeout')
        assert len(phone.frames) == 2
        assert len(owner.frames) == 7
    finally:
        server.unregister_live_transport(phone); server.unregister_live_transport(owner)
