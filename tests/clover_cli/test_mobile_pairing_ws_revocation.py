"""Device transports must not emit queued data after credential revocation."""
import asyncio


def test_revoked_device_transport_closes_before_outbound_frame(tmp_path, monkeypatch):
    from clover_cli import mobile_pairing
    from tui_gateway.ws import WSTransport

    class Socket:
        def __init__(self):
            self.frames = []
            self.close_code = None

        async def send_text(self, text):
            self.frames.append(text)

        async def close(self, code=1000, reason=None):
            self.close_code = code

    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    device = store.create('phone')
    principal = store.authenticate(device['token'])
    assert principal is not None

    async def run():
        socket = Socket()
        transport = WSTransport(socket, asyncio.get_running_loop(), auth_identity={
            'provider': 'mobile-device', 'device_principal': principal,
        })
        assert await transport.write_async({'result': 'before revocation'})
        assert store.revoke(device['id'])
        assert not await transport.write_async({'result': 'secret after revocation'})
        assert len(socket.frames) == 1
        assert socket.close_code == 4401

    asyncio.run(run())


def test_expired_device_cannot_issue_rpc_or_receive_frame(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest
    from clover_cli import mobile_pairing
    from tui_gateway.device_auth import authorize_request, DeviceAccessDenied
    from tui_gateway.ws import WSTransport

    now = [1000.0]
    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json', clock=lambda: now[0])
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)
    device = store.create('phone')
    principal = store.authenticate(device['token'])
    now[0] += mobile_pairing.ACTIVE_TOKEN_TTL_SECONDS + 1
    identity = {'provider': 'mobile-device', 'device_principal': principal}
    with pytest.raises(DeviceAccessDenied):
        authorize_request(SimpleNamespace(auth_identity=identity), 'session.create', {}, {})

    class Socket:
        frames = []
        close_code = None
        async def send_text(self, text): self.frames.append(text)
        async def close(self, code=1000, reason=None): self.close_code = code

    async def run():
        socket = Socket()
        transport = WSTransport(socket, asyncio.get_running_loop(), auth_identity=identity)
        assert not await transport.write_async({'result': 'secret'})
        assert not socket.frames
        assert socket.close_code == 4401
    asyncio.run(run())


def test_device_transport_closes_if_principal_revalidation_fails(tmp_path, monkeypatch):
    from clover_cli import mobile_pairing
    from tui_gateway.ws import WSTransport

    class Socket:
        def __init__(self):
            self.frames = []
            self.close_code = None

        async def send_text(self, text):
            self.frames.append(text)

        async def close(self, code=1000, reason=None):
            self.close_code = code

    store = mobile_pairing.DeviceStore(tmp_path / 'devices.json')
    device = store.create('phone')
    principal = store.authenticate(device['token'])
    assert principal is not None
    monkeypatch.setattr(mobile_pairing, 'get_device_store', lambda: store)

    def fail(_principal):
        raise OSError('device store unavailable')

    monkeypatch.setattr(store, 'principal_is_active', fail)

    async def run():
        socket = Socket()
        transport = WSTransport(socket, asyncio.get_running_loop(), auth_identity={
            'provider': 'mobile-device', 'device_principal': principal,
        })
        assert not await transport.write_async({'result': 'secret'})
        assert not socket.frames
        assert socket.close_code == 4401

    asyncio.run(run())
