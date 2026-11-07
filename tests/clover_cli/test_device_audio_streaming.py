"""ASGI streaming and paired-audio body-bound regression coverage."""
import asyncio
import pytest


async def exercise(chunks, headers=None, path='/api/audio/speak', limit=8):
    from clover_cli.device_audio_body_limit import DeviceAudioBodyLimit
    incoming = list(chunks)
    sent, bodies = [], []
    async def receive():
        if not incoming:
            return {'type': 'http.disconnect'}
        return incoming.pop(0)
    async def send(message):
        sent.append(message)
    async def app(scope, receive, send):
        bodies.append(await receive())
        bodies.append(await receive())
        await send({'type': 'http.response.start', 'status': 204, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})
    scope = {'type': 'http', 'method': 'POST', 'path': path,
             'headers': [(b'authorization', b'Bearer cdt_test')] if headers is None else headers}
    await DeviceAudioBodyLimit(app, speech_limit=limit, transcription_limit=limit)(scope, receive, send)
    return sent, bodies, incoming


def request(body, more=False):
    return {'type': 'http.request', 'body': body, 'more_body': more}


@pytest.mark.parametrize('path', ['/api/audio/speak', '/api/audio/transcribe', '/api/audio/speak/'])
def test_exact_multichunk_replays_once_and_disconnect(path):
    sent, bodies, left = asyncio.run(exercise([request(b'1234', True), request(b'5678')], path=path))
    assert sent[0]['status'] == 204
    assert bodies == [request(b'12345678'), {'type': 'http.disconnect'}]
    assert not left


@pytest.mark.parametrize('headers', [
    [(b'authorization', b'Bearer cdt_test')],
    [(b'authorization', b'bEaReR   cdt_test '), (b'content-length', b'1')],
])
def test_actual_bytes_override_missing_or_false_length(headers):
    sent, bodies, left = asyncio.run(exercise([request(b'1234', True), request(b'56789', True), request(b'excess')], headers))
    assert sent[0]['status'] == 413
    assert not bodies and len(left) == 1


@pytest.mark.parametrize('extra', [
    [(b'content-length', b'8'), (b'content-length', b'8')],
    [(b'content-length', b'-1')], [(b'content-length', b'1, 2')],
    [(b'authorization', b'Bearer owner')],
])
def test_invalid_or_duplicate_headers_rejected(extra):
    sent, bodies, left = asyncio.run(exercise([request(b'123')], [(b'authorization', b'Bearer cdt_test')] + extra))
    assert sent[0]['status'] == 400
    assert not bodies and len(left) == 1


def test_disconnect_during_buffer_does_not_call_app_or_send():
    sent, bodies, left = asyncio.run(exercise([request(b'123', True), {'type': 'http.disconnect'}]))
    assert not sent and not bodies and not left


def test_owner_and_other_paths_not_limited():
    for headers, path in [([(b'authorization', b'Bearer owner')], '/api/audio/speak'), ([(b'authorization', b'Bearer cdt_test')], '/api/other')]:
        sent, bodies, _ = asyncio.run(exercise([request(b'123456789')], headers, path))
        assert sent[0]['status'] == 204
        assert bodies[0]['body'] == b'123456789'
