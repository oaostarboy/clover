import hashlib
from urllib.parse import parse_qs, urlparse

from clover_cli.mobile_pairing import DeviceStore, create_relay_pairing


def test_relay_pairing_uses_scoped_device_and_hash_only_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr('clover_cli.profiles.get_active_profile_name', lambda: 'default')
    now = [1000.0]
    store = DeviceStore(tmp_path / 'devices.json', clock=lambda: now[0])
    result = create_relay_pairing(store)
    query = parse_qs(urlparse(result['uri']).query)
    assert query['origin'] == ['https://relay.cloverai.ca']
    assert query['token'] == [result['token']]
    records = store.relay_devices('default')
    assert records == [{'hash': hashlib.sha256(result['token'].encode()).hexdigest(), 'expiresAt': 1600000}]
    assert store.relay_devices('other') == []
    principal = store.authenticate(result['token'])
    assert principal.profile == 'default'
    assert store.relay_devices('default')[0]['expiresAt'] > 1600000
    store.revoke(result['device']['id'])
    assert store.relay_devices('default') == []
    assert store.authenticate(result['token']) is None


def test_relay_management_routes_require_desktop_owner(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from clover_cli import web_server, mobile_pairing
    monkeypatch.setenv('CLOVER_HOME', str(tmp_path))
    monkeypatch.setattr('clover_cli.profiles.get_active_profile_name', lambda: 'default')
    store = DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(web_server, '_SESSION_TOKEN', 'fixture-desktop-owner')
    monkeypatch.setattr(web_server.app.state, 'auth_required', False, raising=False)
    monkeypatch.setattr(web_server.app.state, 'bound_host', '127.0.0.1', raising=False)
    monkeypatch.setattr(web_server.app.state, 'bound_port', 8123, raising=False)
    with TestClient(web_server.app, base_url='http://127.0.0.1:8123') as client:
        owner = {'Authorization': 'Bearer fixture-desktop-owner'}
        assert client.post('/api/mobile/relay/pairing').status_code == 401
        response = client.post('/api/mobile/relay/pairing', headers=owner)
        assert response.status_code == 200
        device = response.json()
        phone = {'Authorization': 'Bearer ' + device['token']}
        assert client.get('/api/mobile/relay/devices', headers=phone).status_code == 401
        assert client.post('/api/mobile/relay/pairing', headers=phone).status_code == 401
        snapshot = client.get('/api/mobile/relay/devices', headers=owner).json()
        assert snapshot['profile'] == 'default'
        assert len(snapshot['gatewayId']) == 64
        assert int(snapshot['gatewayId'], 16) >= 0
        assert str(store.path) not in str(snapshot)
        first_id = snapshot['gatewayId']
        monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: DeviceStore(tmp_path / 'other' / 'devices.json'))
        assert client.get('/api/mobile/relay/devices', headers=owner).json()['gatewayId'] != first_id
        monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: DeviceStore(tmp_path / 'devices.json'))
        assert client.get('/api/mobile/relay/devices', headers=owner).json()['gatewayId'] == first_id
        assert len(snapshot['devices']) == 1
        assert device['token'] not in str(snapshot)


def test_unused_pairing_expires_and_never_exports_stale_record(tmp_path, monkeypatch):
    monkeypatch.setattr('clover_cli.profiles.get_active_profile_name', lambda: 'default')
    now = [1000.0]
    store = DeviceStore(tmp_path / 'devices.json', clock=lambda: now[0])
    create_relay_pairing(store)
    now[0] = 1601.0
    assert store.relay_devices('default') == []
