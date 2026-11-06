"""Streaming limits run before FastAPI consumes device audio bodies."""
import asyncio
import pytest


@pytest.mark.parametrize('public', [False, True])
def test_production_body_rejected_before_json(monkeypatch, tmp_path, public):
    from clover_cli import web_server, mobile_pairing, profiles
    from fastapi.testclient import TestClient
    from starlette.requests import Request
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'worker')
    monkeypatch.setattr(web_server.app.state, 'auth_required', public, raising=False)
    token = store.create()['token']
    calls = []
    async def forbidden_json(self):
        calls.append(True)
        raise AssertionError('JSON parsed before limit')
    monkeypatch.setattr(Request, 'json', forbidden_json)
    with TestClient(web_server.app, raise_server_exceptions=False) as client:
        response = client.post('/api/audio/speak', headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, content=b'x' * 65537)
    assert response.status_code == 413
    assert not calls


@pytest.mark.parametrize('public', [False, True])
def test_encoded_limit_precedes_decode(monkeypatch, tmp_path, public):
    from clover_cli import web_server, mobile_pairing, profiles
    from fastapi.testclient import TestClient
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'worker')
    monkeypatch.setattr(web_server.app.state, 'auth_required', public, raising=False)
    monkeypatch.setattr(web_server, 'MAX_TRANSCRIPTION_ENCODED_BYTES', 4)
    token = store.create()['token']
    calls = []
    monkeypatch.setattr(web_server.base64, 'b64decode', lambda *a, **k: calls.append(a) or b'a')
    with TestClient(web_server.app) as client:
        response = client.post('/api/audio/transcribe', headers={'Authorization': 'Bearer ' + token}, json={'data_url': 'data:audio/webm;base64,AAAAAAAA'})
    assert response.status_code == 413
    assert not calls


@pytest.mark.parametrize('public', [False, True])
def test_oversize_data_url_header_rejected_before_decode(monkeypatch, tmp_path, public):
    from clover_cli import web_server, mobile_pairing, profiles
    from fastapi.testclient import TestClient
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'worker')
    monkeypatch.setattr(web_server.app.state, 'auth_required', public, raising=False)
    token = store.create()['token']
    calls = []
    monkeypatch.setattr(web_server.base64, 'b64decode', lambda *a, **k: calls.append(a) or b'a')
    with TestClient(web_server.app) as client:
        response = client.post('/api/audio/transcribe', headers={'Authorization': 'Bearer ' + token}, json={'data_url': 'data:audio/webm;' + 'a' * 65536 + ';base64,QQ=='})
    assert response.status_code == 413
    assert not calls


@pytest.mark.parametrize('public', [False, True])
@pytest.mark.parametrize('prefix', ['bearer  ', 'BeArEr '])
def test_device_header_variants_cannot_fall_back_to_owner(monkeypatch, tmp_path, public, prefix):
    from clover_cli import web_server, mobile_pairing, profiles
    from fastapi.testclient import TestClient
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'worker')
    monkeypatch.setattr(web_server.app.state, 'auth_required', public, raising=False)
    token = store.create()['token']
    store.revoke(store.authenticate(token).device_id)
    with TestClient(web_server.app) as client:
        response = client.post('/api/audio/speak', headers={'Authorization': prefix + token, 'X-Clover-Token': web_server._SESSION_TOKEN}, json={'text': 'x'})
    assert response.status_code in (401, 403)


@pytest.mark.parametrize('public', [False, True])
def test_production_transcription_stream_rejected_before_parse(monkeypatch, tmp_path, public):
    from clover_cli import web_server, mobile_pairing, profiles
    from clover_cli.device_audio_body_limit import DEVICE_TRANSCRIPTION_BODY_MAX_BYTES
    from starlette.requests import Request
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'worker')
    monkeypatch.setattr(web_server.app.state, 'auth_required', public, raising=False)
    token = store.create()['token']
    calls = []
    async def forbidden_json(self):
        calls.append('json')
        raise AssertionError('parsed oversized body')
    monkeypatch.setattr(Request, 'json', forbidden_json)
    monkeypatch.setattr(web_server.base64, 'b64decode', lambda *a, **k: calls.append('decode'))
    async def run():
        remaining = DEVICE_TRANSCRIPTION_BODY_MAX_BYTES + 1
        sent = []
        async def receive():
            nonlocal remaining
            if not remaining:
                return {'type': 'http.disconnect'}
            count = min(1024 * 1024, remaining)
            remaining -= count
            return {'type': 'http.request', 'body': b'x' * count, 'more_body': bool(remaining)}
        async def send(message):
            sent.append(message)
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'POST', 'scheme': 'http', 'path': '/api/audio/transcribe', 'raw_path': b'/api/audio/transcribe', 'query_string': b'', 'root_path': '', 'headers': [(b'authorization', ('Bearer ' + token).encode()), (b'content-type', b'application/json')], 'client': ('127.0.0.1', 1), 'server': ('localhost', 80)}
        await web_server.app(scope, receive, send)
        return sent
    sent = asyncio.run(run())
    assert sent[0]['status'] == 413
    assert not calls


@pytest.mark.parametrize('public', [False, True])
def test_repeated_valid_surrogate_escaped_speech(monkeypatch, tmp_path, public):
    import json
    import tools.tts_tool as tts
    from clover_cli import web_server, mobile_pairing, profiles
    from fastapi.testclient import TestClient
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'worker')
    monkeypatch.setattr(web_server, '_resolve_profile_dir', lambda _: tmp_path / 'worker')
    monkeypatch.setattr(web_server.app.state, 'auth_required', public, raising=False)
    seen = []
    monkeypatch.setattr(tts, 'text_to_speech_tool', lambda text: seen.append(text) or {'success': False})
    token = store.create()['token']
    text = '😀' * 4096
    content = json.dumps({'text': text}, ensure_ascii=True)
    with TestClient(web_server.app) as client:
        for _ in range(2):
            response = client.post('/api/audio/speak', headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, content=content)
            assert response.status_code == 400
    assert seen == [text, text]


def test_streaming_device_body_rejected_before_inner_app():
    from clover_cli.device_audio_body_limit import DeviceAudioBodyLimit
    seen = []
    messages = iter([
        {"type": "http.request", "body": b"1234", "more_body": True},
        {"type": "http.request", "body": b"56789", "more_body": False},
    ])
    async def receive():
        return next(messages)
    async def send(message):
        seen.append(message)
    async def app(scope, receive, send):
        raise AssertionError("inner app must not run")
    scope = {"type": "http", "method": "POST", "path": "/api/audio/speak", "headers": [(b"authorization", b"Bearer cdt_test")]}
    asyncio.run(DeviceAudioBodyLimit(app, speech_limit=8)(scope, receive, send))
    assert seen[0]["status"] == 413
