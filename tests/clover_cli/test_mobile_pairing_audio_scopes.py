"""Persisted audio-scope authority for paired devices."""

import json
import pytest


def test_new_pairing_persists_audio_scopes_and_scope_mutation_revokes_principal(tmp_path):
    from clover_cli.mobile_pairing import DeviceStore

    path = tmp_path / "devices.json"
    first = DeviceStore(path)
    created = first.create("phone")
    principal = first.authenticate(created["token"])
    record = json.loads(path.read_text())["devices"][0]
    assert {"audio_transcribe", "audio_speak"} <= set(record["capabilities"])
    assert {"audio_transcribe", "audio_speak"} <= principal.capabilities

    record["capabilities"].remove("audio_speak")
    path.write_text(json.dumps({"version": 1, "devices": [record]}))
    assert not DeviceStore(path).principal_is_active(principal)


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("profile", ["worker", "current"])
def test_device_transcription_uses_its_profile_via_real_asgi(tmp_path, monkeypatch, public, profile):
    from fastapi.testclient import TestClient
    from clover_cli import mobile_pairing, profiles, web_server
    import tools.voice_mode as voice_mode
    from clover_constants import get_clover_home

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: profile)
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda profile: tmp_path / profile)
    store = mobile_pairing.DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(mobile_pairing, "get_device_store", lambda: store)
    monkeypatch.setattr(web_server.app.state, "auth_required", public, raising=False)
    seen = []
    def transcribe(path):
        seen.append(str(get_clover_home()))
        return {"success": True, "transcript": "test", "provider": "stub"}
    monkeypatch.setattr(voice_mode, "transcribe_recording", transcribe)
    token = store.create()["token"]
    with TestClient(web_server.app) as client:
        response = client.post("/api/audio/transcribe", headers={"Authorization": f"Bearer {token}"},
                               json={"data_url": "data:audio/webm;base64,YXVkaW8="})
    assert response.status_code == 200, response.text
    assert response.json()["transcript"] == "test"
    assert seen == [str(tmp_path / profile)]


@pytest.mark.parametrize("public", [False, True])
def test_speech_revoked_during_provider_cannot_return_audio(tmp_path, monkeypatch, public):
    from fastapi.testclient import TestClient
    from clover_cli import mobile_pairing, profiles, web_server
    import tools.tts_tool as tts
    from clover_constants import get_clover_home

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "worker")
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda profile: tmp_path / profile)
    store = mobile_pairing.DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(mobile_pairing, "get_device_store", lambda: store)
    monkeypatch.setattr(web_server.app.state, "auth_required", public, raising=False)
    device = store.create()
    output = tmp_path / "output.mp3"
    seen = []
    def speak(text):
        seen.append(str(get_clover_home()))
        output.write_bytes(b"audio")
        assert mobile_pairing.DeviceStore(store.path).revoke(device["id"])
        return {"success": True, "file_path": str(output), "provider": "stub"}
    monkeypatch.setattr(tts, "text_to_speech_tool", speak)
    with TestClient(web_server.app) as client:
        response = client.post("/api/audio/speak", headers={"Authorization": f"Bearer {device['token']}"}, json={"text": "hello"})
    assert response.status_code in (401, 403), response.text
    assert seen == [str(tmp_path / "worker")]
    assert not output.exists()


@pytest.mark.parametrize("public", [False, True])
def test_device_audio_boundary_and_success(tmp_path, monkeypatch, public):
    from fastapi.testclient import TestClient
    from clover_cli import mobile_pairing, profiles, web_server
    import tools.tts_tool as tts
    from clover_constants import get_clover_home
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "worker")
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda name: tmp_path / name)
    store = mobile_pairing.DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(mobile_pairing, "get_device_store", lambda: store)
    monkeypatch.setattr(web_server.app.state, "auth_required", public, raising=False)
    device = store.create()
    seen = []
    def speak(text):
        seen.append(str(get_clover_home()))
        path = tmp_path / "speak.mp3"
        path.write_bytes(b"audio")
        return {"success": True, "file_path": str(path), "provider": "stub"}
    monkeypatch.setattr(tts, "text_to_speech_tool", speak)
    headers = {"Authorization": f"Bearer {device['token']}"}
    with TestClient(web_server.app) as client:
        response = client.post("/api/audio/speak?profile=worker", headers=headers, json={"text": "hello"})
        assert response.status_code == 200, response.text
        assert response.json()["data_url"] == "data:audio/mpeg;base64,YXVkaW8="
        for method, path in [
            ("POST", "/api/audio/speak?profile=other"),
            ("POST", "/api/audio/speak?profile=worker&profile=worker"),
            ("POST", "/api/audio/speak?unknown=1"),
            ("POST", "/api/audio/%73peak"),
            ("POST", "/api/audio/speak/"),
            ("GET", "/api/audio/speak"),
            ("GET", "/api/audio/voice-config"),
            ("GET", "/api/audio/elevenlabs/voices"),
            ("GET", "/api/settings"),
            ("GET", "/api/config"),
            ("POST", "/api/config"),
            ("POST", "/api/model/set"),
            ("POST", "/api/rpc"),
            ("GET", "/api/models"),
            ("GET", "/api/files"),
        ]:
            response = client.request(method, path, headers=headers, json={"text": "hello"})
            assert response.status_code in (401, 403), (path, response.status_code, response.text)
        assert client.post("/api/audio/speak", headers={"Authorization": "Bearer cdt_invalid"}, json={"text": "x"}).status_code in (401, 403)
        assert client.post("/api/audio/speak", json={"text": "x"}).status_code in (401, 403)
        assert store.revoke(device["id"])
        assert client.post("/api/audio/speak", headers=headers, json={"text": "x"}).status_code in (401, 403)
    assert seen == [str(tmp_path / "worker")]
    assert not (tmp_path / "speak.mp3").exists()


@pytest.mark.parametrize("caps", [None, "audio_speak", ["audio_speak", "audio_speak"], ["unknown"], [1]])
def test_malformed_capability_records_refuse_authentication(tmp_path, caps):
    from clover_cli.mobile_pairing import DeviceStore
    path = tmp_path / "devices.json"
    store = DeviceStore(path)
    token = store.create()["token"]
    data = json.loads(path.read_text())
    data["devices"][0]["capabilities"] = caps
    path.write_text(json.dumps(data))
    assert DeviceStore(path).authenticate(token) is None


def test_legacy_profile_record_does_not_gain_audio(tmp_path):
    from clover_cli.mobile_pairing import DeviceStore, DEVICE_CAPABILITIES
    path = tmp_path / "devices.json"
    store = DeviceStore(path)
    token = store.create()["token"]
    data = json.loads(path.read_text())
    del data["devices"][0]["capabilities"]
    path.write_text(json.dumps(data))
    principal = DeviceStore(path).authenticate(token)
    assert principal is not None
    assert principal.capabilities == DEVICE_CAPABILITIES


@pytest.mark.parametrize("public", [False, True])
def test_transcribe_revoked_during_provider_deletes_input_and_hides_result(tmp_path, monkeypatch, public):
    from fastapi.testclient import TestClient
    from clover_cli import mobile_pairing, profiles, web_server
    import tools.voice_mode as voice
    from pathlib import Path
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "worker")
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda name: tmp_path / name)
    store = mobile_pairing.DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(mobile_pairing, "get_device_store", lambda: store)
    monkeypatch.setattr(web_server.app.state, "auth_required", public, raising=False)
    device = store.create()
    paths = []
    def transcribe(path):
        paths.append(Path(path))
        assert paths[-1].exists()
        assert mobile_pairing.DeviceStore(store.path).revoke(device["id"])
        return {"success": True, "transcript": "secret"}
    monkeypatch.setattr(voice, "transcribe_recording", transcribe)
    with TestClient(web_server.app) as client:
        response = client.post("/api/audio/transcribe", headers={"Authorization": f"Bearer {device['token']}"}, json={"data_url": "data:audio/webm;base64,YXVkaW8="})
    assert response.status_code in (401, 403)
    assert "secret" not in response.text
    assert len(paths) == 1 and not paths[0].exists()


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("case", ["legacy", "wrong_profile", "expired", "unknown", "no_transcribe", "no_speak"])
def test_device_scope_denials_via_asgi(tmp_path, monkeypatch, public, case):
    from fastapi.testclient import TestClient
    from clover_cli import mobile_pairing, profiles, web_server
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "worker")
    now = [1000.0]
    path = tmp_path / "devices.json"
    store = mobile_pairing.DeviceStore(path, clock=lambda: now[0])
    monkeypatch.setattr(mobile_pairing, "get_device_store", lambda: store)
    monkeypatch.setattr(web_server.app.state, "auth_required", public, raising=False)
    device = store.create()
    data = json.loads(path.read_text())
    record = data["devices"][0]
    if case == "legacy":
        del record["capabilities"]
    elif case == "wrong_profile":
        record["profile"] = "other"
    elif case == "expired":
        now[0] += mobile_pairing.ACTIVE_TOKEN_TTL_SECONDS + 1
    elif case == "unknown":
        record["capabilities"].append("admin")
    else:
        record["capabilities"].remove("audio_transcribe" if case == "no_transcribe" else "audio_speak")
    path.write_text(json.dumps(data))
    endpoint = "transcribe" if case == "no_transcribe" else "speak"
    body = {"data_url": "data:audio/webm;base64,YXVkaW8="} if endpoint == "transcribe" else {"text": "hello"}
    with TestClient(web_server.app) as client:
        response = client.post(f"/api/audio/{endpoint}", headers={"Authorization": f"Bearer {device['token']}"}, json=body)
    assert response.status_code in (401, 403), response.text


@pytest.mark.parametrize("public", [False, True])
def test_failed_speech_preserves_provider_asserted_file_and_hides_provider_details(tmp_path, monkeypatch, public):
    from fastapi.testclient import TestClient
    from clover_cli import mobile_pairing, profiles, web_server
    import tools.tts_tool as tts
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "worker")
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda name: tmp_path / name)
    store = mobile_pairing.DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(mobile_pairing, "get_device_store", lambda: store)
    monkeypatch.setattr(web_server.app.state, "auth_required", public, raising=False)
    device = store.create()
    output = tmp_path / "private.mp3"
    def speak(text):
        output.write_bytes(b"private")
        return {"success": False, "file_path": str(output), "error": "private-provider-secret"}
    monkeypatch.setattr(tts, "text_to_speech_tool", speak)
    with TestClient(web_server.app) as client:
        response = client.post("/api/audio/speak", headers={"Authorization": f"Bearer {device['token']}"}, json={"text": "hello"})
    assert response.status_code == 400
    assert "private-provider-secret" not in response.text
    assert output.read_bytes() == b"private"


@pytest.mark.parametrize("public", [False, True])
def test_device_token_cannot_be_promoted_by_service_token_seam(tmp_path, monkeypatch, public):
    from fastapi.testclient import TestClient
    from clover_cli import mobile_pairing, profiles, web_server
    from clover_cli.dashboard_auth import token_auth
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "worker")
    store = mobile_pairing.DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(mobile_pairing, "get_device_store", lambda: store)
    monkeypatch.setattr(web_server.app.state, "auth_required", public, raising=False)
    monkeypatch.setattr(token_auth, "is_token_route", lambda path: path == "/api/audio/voice-config")
    monkeypatch.setattr(token_auth, "authenticate_token", lambda request: (object(), None))
    token = store.create()["token"]
    with TestClient(web_server.app) as client:
        response = client.get("/api/audio/voice-config", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code in (401, 403), response.text


@pytest.mark.parametrize("public", [False, True])
def test_provider_http_exception_does_not_leak_detail(tmp_path, monkeypatch, public):
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from clover_cli import mobile_pairing, profiles, web_server
    import tools.voice_mode as voice
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "worker")
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda name: tmp_path / name)
    store = mobile_pairing.DeviceStore(tmp_path / "devices.json")
    monkeypatch.setattr(mobile_pairing, "get_device_store", lambda: store)
    monkeypatch.setattr(web_server.app.state, "auth_required", public, raising=False)
    def transcribe(path):
        raise HTTPException(status_code=400, detail="provider-secret")
    monkeypatch.setattr(voice, "transcribe_recording", transcribe)
    device = store.create()
    with TestClient(web_server.app) as client:
        response = client.post("/api/audio/transcribe", headers={"Authorization": f"Bearer {device['token']}"}, json={"data_url": "data:audio/webm;base64,YXVkaW8="})
    assert response.status_code >= 400
    assert "provider-secret" not in response.text
