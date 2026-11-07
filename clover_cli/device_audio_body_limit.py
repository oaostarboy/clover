"""Bound paired-device audio request bodies before framework parsing."""
from starlette.responses import JSONResponse

DEVICE_SPEECH_BODY_MAX_BYTES = 64 * 1024
MAX_TRANSCRIPTION_DECODED_BYTES = 25 * 1024 * 1024
MAX_TRANSCRIPTION_ENCODED_BYTES = 4 * ((MAX_TRANSCRIPTION_DECODED_BYTES + 2) // 3)
DEVICE_TRANSCRIPTION_BODY_MAX_BYTES = MAX_TRANSCRIPTION_ENCODED_BYTES + 64 * 1024


class DeviceAudioBodyLimit:
    def __init__(self, app, speech_limit=DEVICE_SPEECH_BODY_MAX_BYTES,
                 transcription_limit=DEVICE_TRANSCRIPTION_BODY_MAX_BYTES):
        self.app = app
        self.limits = {'/api/audio/speak': speech_limit,
                       '/api/audio/transcribe': transcription_limit}

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '').rstrip('/')
        limit = self.limits.get(path) if scope.get('method') == 'POST' else None
        if scope['type'] != 'http' or limit is None:
            return await self.app(scope, receive, send)
        headers = scope.get('headers', [])
        auth = [v.decode('latin-1') for k, v in headers if k.lower() == b'authorization']
        def device(value):
            parts = value.split(' ', 1)
            return len(parts) == 2 and parts[0].strip().lower() == 'bearer' and parts[1].strip().startswith('cdt_')
        if not any(device(value) for value in auth):
            return await self.app(scope, receive, send)
        lengths = [v for k, v in headers if k.lower() == b'content-length']
        if len(auth) != 1 or len(lengths) > 1 or any(not v or not v.isdigit() for v in lengths):
            return await JSONResponse({'detail': 'Invalid request headers'}, status_code=400)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            if message['type'] != 'http.request':
                raise RuntimeError('Unexpected ASGI receive message')
            chunk = message.get('body', b'')
            if len(body) + len(chunk) > limit:
                return await JSONResponse({'detail': 'Request body too large'}, status_code=413)(scope, receive, send)
            body.extend(chunk)
            if not message.get('more_body', False):
                break
        body = bytes(body)
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()
        return await self.app(scope, replay, send)
