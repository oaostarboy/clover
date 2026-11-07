"""Device principal lifetime and immutable authority regression tests."""


def test_principal_carries_profile_and_expires_or_revokes(tmp_path):
    from dataclasses import FrozenInstanceError
    import pytest
    from clover_cli.mobile_pairing import DeviceStore, ACTIVE_TOKEN_TTL_SECONDS

    now = [1000.0]
    store = DeviceStore(tmp_path / 'devices.json', clock=lambda: now[0])
    created = store.create('phone')
    principal = store.authenticate(created['token'])
    assert principal is not None
    assert principal.device_id == created['id']
    assert principal.profile == 'default'
    assert 'chat' in principal.capabilities
    with pytest.raises(FrozenInstanceError):
        principal.profile = 'other'
    assert store.principal_is_active(principal)
    assert store.revoke(created['id'])
    assert not store.principal_is_active(principal)
    created = store.create('phone')
    principal = store.authenticate(created['token'])
    now[0] += ACTIVE_TOKEN_TTL_SECONDS + 1
    assert not store.principal_is_active(principal)
