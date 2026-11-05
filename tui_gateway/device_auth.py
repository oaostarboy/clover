"""Device-specific RPC authority. Owner transports retain their existing policy."""
from pathlib import Path


class DeviceAccessDenied(PermissionError):
    pass


# Exact capabilities and accepted parameters, not a wildcard RPC namespace.
_RULES = {
    'session.create': ('chat', {'profile', 'source', 'model', 'provider'}),
    'session.resume': ('history', {'profile', 'session_id'}),
    'session.history': ('history', {'session_id'}),
    'prompt.submit': ('chat', {'session_id', 'text'}),
    'session.interrupt': ('chat', {'session_id'}),
    'model.options': ('model_selection', {'profile', 'session_id', 'refresh'}),
    'config.set': ('model_selection', {'session_id', 'key', 'value', 'confirm_expensive_model'}),
    'image.attach_bytes': ('media', {'session_id', 'content_base64', 'filename'}),
    'file.attach': ('media', {'session_id', 'data_url', 'name'}),
}


def device_principal(transport):
    from clover_cli.mobile_pairing import DevicePrincipal, get_device_store
    identity = getattr(transport, 'auth_identity', None)
    if not isinstance(identity, dict):
        return None
    if identity.get('provider') != 'mobile-device' and identity.get('device_principal') is None:
        return None
    principal = identity.get('device_principal')
    try:
        active = isinstance(principal, DevicePrincipal) and get_device_store().principal_is_active(principal)
    except Exception as exc:
        raise DeviceAccessDenied('device authorization unavailable') from exc
    if not active:
        raise DeviceAccessDenied('device authorization expired or revoked')
    return principal


def profile_home(principal):
    from clover_cli.profiles import get_profile_dir
    try:
        home = Path(get_profile_dir(principal.profile)).resolve()
        if not home.is_dir():
            raise DeviceAccessDenied('device profile unavailable')
        return home
    except DeviceAccessDenied:
        raise
    except Exception as exc:
        raise DeviceAccessDenied('device profile unavailable') from exc


def ensure_session_owned(transport, session, *, launch_home=None):
    principal = device_principal(transport)
    if principal is None:
        return
    actual = session.get('profile_home') or launch_home
    if actual is None or Path(actual).resolve() != profile_home(principal):
        raise DeviceAccessDenied('session is outside device profile')


def authorize_request(transport, method, params, sessions, *, launch_home=None):
    principal = device_principal(transport)
    if principal is None:
        return params
    rule = _RULES.get(method)
    if rule is None or rule[0] not in principal.capabilities:
        raise DeviceAccessDenied('operation is not permitted for this device')
    if set(params) - rule[1]:
        raise DeviceAccessDenied('unsupported device parameters')
    if method == 'config.set':
        value = params.get('value')
        if (params.get('key') != 'model' or not isinstance(value, str)
                or not value or any(ch.isspace() for ch in value) or value.startswith('-')):
            raise DeviceAccessDenied('only session model selection is permitted')
    requested = params.get('profile')
    if requested is not None and requested != principal.profile:
        raise DeviceAccessDenied('device profile mismatch')
    expected = profile_home(principal)
    sid = params.get('session_id')
    if sid:
        if not isinstance(sid, str):
            raise DeviceAccessDenied('invalid session identifier')
        session = sessions.get(sid)
        if session is not None:
            ensure_session_owned(transport, session, launch_home=launch_home)
        elif method != 'session.resume':
            raise DeviceAccessDenied('session unavailable')
    elif method not in {'session.create', 'model.options'}:
        raise DeviceAccessDenied('session identifier required')
    scoped = dict(params)
    if method in {'session.create', 'session.resume', 'model.options'}:
        scoped['profile'] = principal.profile
    return scoped
