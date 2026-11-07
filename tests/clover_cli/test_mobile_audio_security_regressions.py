"""Adversarial paired-device speech regressions; no real provider calls."""
import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("text", [pytest.param("x" * 4097, id="limit-plus-one"), pytest.param("x" * 1000000, id="million"), pytest.param(" " * 4097 + "x", id="whitespace"), pytest.param("é" * 4097, id="unicode")])
def test_oversize_raw_speech_rejected_before_provider(tmp_path, monkeypatch, public, text):
    from clover_cli import mobile_pairing, profiles, web_server
    import tools.tts_tool as tts
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "worker")
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda _: tmp_path / "worker")
    store = mobile_pairing.DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(mobile_pairing, "get_device_store", lambda: store)
    monkeypatch.setattr(web_server.app.state, "auth_required", public, raising=False)
    seen = []
    monkeypatch.setattr(tts, "text_to_speech_tool", lambda text: seen.append(text) or {"success": False})
    token = store.create()["token"]
    with TestClient(web_server.app) as client:
        response = client.post('/api/audio/speak', headers={'Authorization': 'Bearer ' + token}, json={'text': text})
    assert response.status_code == 413
    assert not seen


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("text", ["a" * 4096, "é" * 4096])
def test_exact_limit_reaches_provider(tmp_path, monkeypatch, public, text):
    from clover_cli import mobile_pairing, profiles, web_server
    import tools.tts_tool as tts
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "worker")
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda _: tmp_path / "worker")
    store = mobile_pairing.DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(mobile_pairing, "get_device_store", lambda: store)
    monkeypatch.setattr(web_server.app.state, "auth_required", public, raising=False)
    seen = []
    monkeypatch.setattr(tts, "text_to_speech_tool", lambda value: seen.append(value) or {"success": False})
    token = store.create()["token"]
    with TestClient(web_server.app) as client:
        response = client.post('/api/audio/speak', headers={'Authorization': 'Bearer ' + token}, json={'text': text})
    assert response.status_code == 400
    assert seen == [text]


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_inactive_device_cannot_reach_provider_with_oversize_text(tmp_path, monkeypatch, public, state):
    from clover_cli import mobile_pairing, profiles, web_server
    import tools.tts_tool as tts
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "worker")
    now = [1000.0]
    store = mobile_pairing.DeviceStore(tmp_path / "devices.json", clock=lambda: now[0])
    monkeypatch.setattr(mobile_pairing, "get_device_store", lambda: store)
    monkeypatch.setattr(web_server.app.state, "auth_required", public, raising=False)
    seen = []
    monkeypatch.setattr(tts, "text_to_speech_tool", lambda text: seen.append(text))
    device = store.create()
    if state == "revoked":
        assert store.revoke(device['id'])
    else:
        now[0] += mobile_pairing.ACTIVE_TOKEN_TTL_SECONDS + 1
    with TestClient(web_server.app) as client:
        response = client.post('/api/audio/speak', headers={'Authorization': 'Bearer ' + device['token']}, json={'text': 'x' * 4097})
    assert response.status_code in (401, 403)
    assert not seen


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("case", ["failed", "missing-success", "malformed-json", "revoked-failure", "symlink"])
def test_failure_never_unlinks_unowned_path(tmp_path, monkeypatch, public, case):
    from clover_cli import mobile_pairing, profiles, web_server
    import tools.tts_tool as tts
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "worker")
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda _: tmp_path / "worker")
    store = mobile_pairing.DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(mobile_pairing, "get_device_store", lambda: store)
    monkeypatch.setattr(web_server.app.state, "auth_required", public, raising=False)
    device = store.create()
    victim = tmp_path / "unrelated"
    victim.write_text("keep")
    link = tmp_path / "link"
    if case == "symlink":
        try:
            link.symlink_to(victim)
        except OSError:
            pytest.skip("symlink unavailable")
    def speak(text):
        if case == "revoked-failure":
            assert store.revoke(device['id'])
        if case == "malformed-json":
            return '{"file_path": ' + repr(str(victim))
        result = {"file_path": str(link if case == "symlink" else victim)}
        if case != "missing-success":
            result['success'] = False
        return result
    monkeypatch.setattr(tts, "text_to_speech_tool", speak)
    with TestClient(web_server.app) as client:
        response = client.post('/api/audio/speak', headers={'Authorization': 'Bearer ' + device['token']}, json={'text': 'hello'})
    assert response.status_code in (400, 401, 403, 500)
    assert victim.read_text() == "keep"
    if case == "symlink":
        assert link.is_symlink()


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("profile", ["default", "current"])
def test_real_profile_resolution_for_device_speech(tmp_path, monkeypatch, public, profile):
    from clover_cli import mobile_pairing, web_server
    from clover_constants import get_clover_home
    import tools.tts_tool as tts
    home = tmp_path / '.clover'
    target = home if profile == 'default' else home / 'profiles' / 'current'
    target.mkdir(parents=True)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('CLOVER_HOME', str(target))
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(web_server.app.state, 'auth_required', public, raising=False)
    seen = []
    monkeypatch.setattr(tts, 'text_to_speech_tool', lambda text: seen.append(get_clover_home()) or {'success': False})
    device = store.create()
    with TestClient(web_server.app) as client:
        response = client.post('/api/audio/speak', headers={'Authorization': 'Bearer ' + device['token']}, json={'text': 'hello'})
    assert response.status_code == 400, response.text
    assert seen == [target]


@pytest.mark.parametrize('public', [False, True])
def test_empty_capabilities_denies_websocket_chat(tmp_path, monkeypatch, public):
    import json
    from clover_cli import mobile_pairing, profiles, web_server
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'worker')
    path = tmp_path / 'devices.json'
    store = mobile_pairing.DeviceStore(path)
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(web_server.app.state, 'auth_required', public, raising=False)
    device = store.create()
    data = json.loads(path.read_text())
    data['devices'][0]['capabilities'] = []
    path.write_text(json.dumps(data))
    with TestClient(web_server.app) as client:
        response = client.post('/api/auth/ws-ticket', headers={'Authorization': 'Bearer ' + device['token']})
        assert response.status_code == 200
        with client.websocket_connect('/api/ws?ticket=' + response.json()['ticket']) as ws:
            ws.receive_json()
            ws.send_json({'jsonrpc': '2.0', 'id': 1, 'method': 'session.create', 'params': {}})
            while True:
                result = ws.receive_json()
                if result.get('id') == 1:
                    break
            assert result['error']['code'] == 4030
