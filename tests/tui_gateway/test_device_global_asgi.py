from fastapi.testclient import TestClient


def test_asgi_reclaim_scope(tmp_path, monkeypatch):
    from clover_cli import mobile_pairing, web_server, profiles
    from clover_cli.dashboard_auth.ws_tickets import _reset_for_tests
    from tui_gateway import server
    home = tmp_path / 'home'; home.mkdir()
    foreign = tmp_path / 'other'; foreign.mkdir()
    monkeypatch.setenv('CLOVER_HOME', str(home))
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'default')
    monkeypatch.setattr(profiles, 'get_profile_dir', lambda name: home if name == 'default' else foreign)
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(web_server.app.state, 'auth_required', True, raising=False)
    monkeypatch.setattr(web_server.app.state, 'bound_host', 'pair.example', raising=False)
    monkeypatch.setattr(web_server.app.state, 'bound_port', 443, raising=False)
    monkeypatch.setattr(server, '_ensure_skin_watcher', lambda: None)
    _reset_for_tests()
    device = store.create()
    session = {'_sid':'foreign-live-id', 'session_key':'foreign-stored-id', 'profile_home':str(foreign)}
    monkeypatch.setitem(server._sessions, 'foreign-live-id', session)
    try:
        with TestClient(web_server.app, base_url='https://pair.example') as client:
            response = client.post('/api/auth/ws-ticket', headers={'Authorization':'Bearer ' + device['token']})
            assert response.status_code == 200
            with client.websocket_connect('wss://pair.example/api/ws?ticket=' + response.json()['ticket']) as ws:
                assert ws.receive_json()['params']['type'] == 'gateway.ready'
                ws.send_json({'jsonrpc':'2.0','id':1,'method':'session.history','params':{'session_id':'foreign-live-id'}})
                assert ws.receive_json()['error']['code'] == 4030
                server._announce_session_reclaimed(session, 'idle_timeout')
                server._announce_session_reclaimed({'_sid':'own-live', 'session_key':'own-stored', 'profile_home':str(home)}, 'idle_timeout')
                ws.send_json({'jsonrpc':'2.0','id':2,'method':'forbidden.barrier','params':{}})
                frames = []
                while True:
                    frame = ws.receive_json(); frames.append(frame)
                    if frame.get('id') == 2: break
                reclaimed = [f['params']['payload'] for f in frames if f.get('params', {}).get('type') == 'session.reclaimed']
                assert reclaimed == [{'session_id':'own-live','stored_session_id':'own-stored','reason':'idle_timeout'}]
    finally:
        _reset_for_tests()
