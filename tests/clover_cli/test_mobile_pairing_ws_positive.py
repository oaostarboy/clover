"""A paired phone can establish an authenticated chat session over the real WS."""
from fastapi.testclient import TestClient


def test_loopback_desktop_accepts_only_device_ticket_auth(tmp_path, monkeypatch):
    from clover_cli import mobile_pairing, web_server
    monkeypatch.setenv('CLOVER_HOME', str(tmp_path / 'home'))
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(web_server.app.state, 'auth_required', False, raising=False)
    monkeypatch.setattr(web_server.app.state, 'bound_host', '127.0.0.1', raising=False)
    monkeypatch.setattr(web_server.app.state, 'bound_port', 80, raising=False)
    device = store.create('phone')
    with TestClient(web_server.app, base_url='http://127.0.0.1') as client:
        headers = {'Authorization': 'Bearer ' + device['token']}
        ticket = client.post('/api/auth/ws-ticket', headers=headers)
        assert ticket.status_code == 200
        assert client.get('/api/config', headers=headers).status_code == 401
        with client.websocket_connect('ws://127.0.0.1/api/ws?ticket=' + ticket.json()['ticket']) as ws:
            assert ws.receive_json()['params']['type'] == 'gateway.ready'
        # Unrelated subprotocols must not disable existing loopback owner auth.
        with client.websocket_connect('ws://127.0.0.1/api/ws?token=' + web_server._SESSION_TOKEN,
                                      subprotocols=['unrelated']) as ws:
            assert ws.receive_json()['params']['type'] == 'gateway.ready'


def test_paired_device_can_create_chat_session_over_websocket(tmp_path, monkeypatch):
    from clover_cli import mobile_pairing, web_server
    from clover_cli.dashboard_auth.ws_tickets import _reset_for_tests
    from clover_state import SessionDB

    monkeypatch.setenv('CLOVER_HOME', str(tmp_path / 'home'))
    db = SessionDB()
    db.close()
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(web_server.app.state, 'auth_required', True, raising=False)
    monkeypatch.setattr(web_server.app.state, 'bound_host', 'pair.example', raising=False)
    monkeypatch.setattr(web_server.app.state, 'bound_port', 443, raising=False)
    from tui_gateway import ws as ws_module, server
    monkeypatch.setattr(server, 'register_live_transport', lambda transport: None)
    monkeypatch.setattr(server, '_ensure_skin_watcher', lambda: None)
    monkeypatch.setattr(ws_module, '_DEVICE_REVALIDATE_INTERVAL_S', 0.05, raising=False)
    _reset_for_tests()
    device = store.create('phone')
    with TestClient(web_server.app, base_url='https://pair.example') as client:
        assert web_server._ws_auth_mode() == 'gated'
        ticket_response = client.post('/api/auth/ws-ticket', headers={
            'Authorization': 'Bearer ' + device['token'],
        })
        assert ticket_response.status_code == 200
        ticket = ticket_response.json()['ticket']
        with client.websocket_connect('wss://pair.example/api/ws?ticket=' + ticket) as ws:
            ws.receive_json()  # gateway.ready
            ws.send_json({'jsonrpc': '2.0', 'id': 1, 'method': 'session.create', 'params': {}})
            while True:
                response = ws.receive_json()
                if response.get('id') == 1:
                    break
            assert 'result' in response, response
            sid = response['result']['session_id']
            ws.send_json({'jsonrpc': '2.0', 'id': 2, 'method': 'session.history', 'params': {'session_id': sid}})
            while True:
                history = ws.receive_json()
                if history.get('id') == 2:
                    break
            assert 'result' in history, history
            ws.send_json({'jsonrpc': '2.0', 'id': 3, 'method': 'config.set', 'params': {}})
            while True:
                denied = ws.receive_json()
                if denied.get('id') == 3:
                    break
            assert denied['error']['code'] == 4030
            assert store.revoke(device['id'])
            # An idle revoked connection must close without another client frame.
            from starlette.websockets import WebSocketDisconnect
            import pytest
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 4401
        assert client.post('/api/auth/ws-ticket', headers={
            'Authorization': 'Bearer ' + device['token'],
        }).status_code == 401
    _reset_for_tests()
