"""Device-ticket admission on the actual dashboard ASGI application."""
from fastapi.testclient import TestClient


def test_device_can_mint_ticket_but_not_access_admin(tmp_path, monkeypatch):
    from clover_cli import web_server
    from clover_cli import mobile_pairing
    from clover_cli.dashboard_auth.ws_tickets import _reset_for_tests

    monkeypatch.setenv('CLOVER_HOME', str(tmp_path / 'home'))
    from clover_state import SessionDB
    db = SessionDB()
    db.close()
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(web_server.app.state, 'auth_required', True, raising=False)
    monkeypatch.setattr(web_server.app.state, 'bound_host', 'pair.example', raising=False)
    monkeypatch.setattr(web_server.app.state, 'bound_port', 443, raising=False)
    _reset_for_tests()
    device = store.create()
    client = TestClient(web_server.app, base_url='https://pair.example')
    headers = {'Authorization': 'Bearer ' + device['token']}
    history = client.get('/api/sessions?limit=40&offset=0&min_messages=0&archived=exclude&order=recent', headers=headers)
    assert history.status_code == 200
    assert isinstance(history.json()['sessions'], list)
    assert client.get('/api/sessions?profile=other', headers=headers).status_code in (401, 403)
    response = client.post('/api/auth/ws-ticket', headers=headers)
    assert response.status_code == 200
    ticket = response.json()['ticket']
    assert ticket
    # Until RPC authorization is implemented, no WS endpoint may upgrade a
    # mobile ticket to the dashboard owner's privileges.
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/api/console?ticket=' + ticket):
            pass
    response = client.get('/api/mobile/devices', headers=headers)
    assert response.status_code in (401, 403)
    assert store.revoke(device['id'])
    assert client.get('/api/sessions', headers=headers).status_code in (401, 403)
    assert client.post('/api/auth/ws-ticket', headers=headers).status_code in (401, 403)
    _reset_for_tests()
