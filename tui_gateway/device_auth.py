"""Device-specific RPC authority. Owner transports retain their existing policy."""
from pathlib import Path


class DeviceAccessDenied(PermissionError):
    """Refusal for a paired device. ``data`` (if any) is the JSON-RPC ``error.data``:
    ``{capability, reason: not_granted|pending|expired}``. Never closes the socket."""

    def __init__(self, message='', data=None):
        super().__init__(message)
        self.data = data


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
    # Live-chat helpers the app calls on a session it already owns (ownership checked below).
    'approval.pending': ('chat', {'session_id'}),
    'approval.received': ('chat', {'session_id', 'request_id'}),
    'approval.respond': ('chat', {'session_id', 'request_id', 'choice', 'all'}),
    'subagent.steer': ('chat', {'session_id', 'subagent_id', 'text'}),
    'session.events.since': ('history', {'session_id', 'last_seen'}),
}


# Methods any paired device may call with no capability (a pending phone must be able
# to ask the owner and poll the answer).
_OPEN = {
    'device.status': set(),
    'device.request_access': {'capabilities'},
}

# Crew rights. crew_read: look at the owner's other agents. crew_act: talk as them.
_CREW_READ = {
    'crew.list': set(),
    'profiles.list': {'include_sessions'},
    # session.list for the device's OWN profile needs only 'history'; crew_read is for other profiles.
    'session.list': {'profile', 'limit', 'offset', 'helper_parent_id', 'title', 'min_messages', 'order', 'archived'},
}
# The owner's checklist and the app's other read/act tabs: no profile, no session, existing rights.
_TODO_PARAMS = {
    'todos.add': {'text'},
    'todos.update': {'id', 'done', 'text', 'notes', 'due_at', 'remind_at', 'owner'},
    'todos.delete': {'id'},
    'todos.reorder': {'ids'},
}
_APP = {
    'agents.list': ('chat', set()),
    'delegation.status': ('chat', {'recent_limit'}),
    'subagent.interrupt': ('chat', {'subagent_id'}),
    'push.register': ('chat', {'token', 'env', 'device_name'}),
    'push.unregister': ('chat', {'token'}),
    'push.test': ('chat', set()),
    'app.state': ('chat', {'background'}),
    'clarify.respond': ('chat', {'session_id', 'request_id', 'answer', 'question_id'}),
    # Creations tab: the device's own generated files (bytes/thumbs go through the HTTP gate).
    'artifacts.list': ('media', {'limit', 'kind', 'cursor', 'refresh'}),
}
_CRON_ACTIONS = {'list', 'pause', 'resume'}
_CREW_ACT = {
    'peer.turn': {'peer', 'text', 'room_id', 'session_hint'},
    # Crew rooms are saved as ui_meta on the owner's default profile; nothing else.
    'profiles.configure': {'name', 'ui_meta', 'ui_meta_expected_revisions'},
    'todos.add': None, 'todos.update': None, 'todos.approve': None, 'todos.delete': None, 'todos.reorder': None,
}
_CREW_SESSION_PARAMS = {
    'session.create': {'profile', 'source', 'model', 'provider'},
    'session.resume': {'profile', 'session_id', 'omit_messages', 'defer_history', 'cols'},
    'session.history': {'session_id'},
    'prompt.submit': {'session_id', 'text'},
    'session.interrupt': {'session_id'},
}

# Phone desk control (relay-safe). control_drive is the only right that reaches these.
_DESK = {'desk.take_control': {'display'}, 'desk.input': {'events'}, 'desk.release': set()}

_AUDIT_SEEN = {}


def _audit(principal, capability, method, decision):
    """One audit line per (device, method, decision) per 30 s. No params, no tokens."""
    import time
    key = (principal.device_id, method, decision)
    now = time.monotonic()
    if now - _AUDIT_SEEN.get(key, -60.0) < 30.0:
        return
    if len(_AUDIT_SEEN) > 2048:
        _AUDIT_SEEN.clear()
    _AUDIT_SEEN[key] = now
    try:
        from clover_cli.mobile_pairing import get_device_store
        get_device_store().audit(principal.device_id, capability, method, decision)
    except Exception:
        pass


def _refuse(principal, capability, method, message='operation is not permitted for this device'):
    from clover_cli.mobile_pairing import denial_reason
    data = denial_reason(principal, capability)
    if capability in ('crew_read', 'crew_act', 'control_view', 'control_drive'):
        _audit(principal, capability, method, 'deny')
    raise DeviceAccessDenied(message, data)


def crew_profile_names():
    """Local profile names the owner's Crew shows (``crew.list`` local rows)."""
    from clover_cli.profiles import list_profiles
    return {p.name for p in list_profiles()}


def device_homes(principal):
    """Profile homes this device may touch: its own, plus every crew profile with crew_act."""
    homes = {profile_home(principal)}
    if 'crew_act' in principal.capabilities:
        from clover_cli.profiles import get_profile_dir
        for name in crew_profile_names():
            try:
                home = Path(get_profile_dir(name)).resolve()
            except Exception:
                continue
            if home.is_dir():
                homes.add(home)
    return homes


def effective_profile(principal, requested):
    """Profile a device request acts as: its own unless crew_act names a crew profile."""
    if requested in (None, '', principal.profile):
        return principal.profile
    if 'crew_act' in principal.capabilities and requested in crew_profile_names():
        return requested
    raise DeviceAccessDenied('device profile mismatch')


def device_principal(transport):
    from clover_cli.mobile_pairing import DevicePrincipal, get_device_store
    identity = getattr(transport, 'auth_identity', None)
    if not isinstance(identity, dict):
        return None
    if identity.get('provider') != 'mobile-device' and identity.get('device_principal') is None:
        return None
    principal = identity.get('device_principal')
    try:
        # Re-read persisted state: rights in force NOW, not those at connect time.
        current = get_device_store().current_principal(principal) if isinstance(principal, DevicePrincipal) else None
    except Exception as exc:
        raise DeviceAccessDenied('device authorization unavailable') from exc
    if current is None:
        raise DeviceAccessDenied('device authorization expired or revoked')
    return current


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
    if actual is None or Path(actual).resolve() not in device_homes(principal):
        raise DeviceAccessDenied('session is outside device profile')


def _need_any(principal, caps, method):
    if not any(c in principal.capabilities for c in caps):
        _refuse(principal, caps[0], method)


def _authorize_app_surface(transport, principal, method, params, sessions, launch_home):
    """Rules for the app's own tabs. Returns scoped params, or None to continue with the generic rules."""
    if method == 'session.list':
        requested = params.get('profile')
        if requested not in (None, '', principal.profile):
            return None  # another profile: crew_read path
        _need_any(principal, ('history',), method)
        if set(params) - _CREW_READ[method]:
            raise DeviceAccessDenied('unsupported device parameters')
        return {**params, 'profile': principal.profile}
    if method == 'todos.list':
        _need_any(principal, ('chat', 'history'), method)
        if params:
            raise DeviceAccessDenied('unsupported device parameters')
        return params
    if method in _TODO_PARAMS:
        if 'assignee' in params:
            return None  # assigning to another agent is a crew action
        _need_any(principal, ('chat', 'history'), method)
        if set(params) - _TODO_PARAMS[method]:
            raise DeviceAccessDenied('unsupported device parameters')
        return params
    if method == 'cron.manage':
        _need_any(principal, ('chat',), method)
        if set(params) - {'action', 'include_disabled', 'name'} or params.get('action', 'list') not in _CRON_ACTIONS:
            raise DeviceAccessDenied('only listing, pausing and resuming routines is permitted')
        return params
    if method == 'config.get':
        _need_any(principal, ('model_selection',), method)
        if params.get('key') != 'provider' or set(params) - {'key'}:
            raise DeviceAccessDenied('only the model provider may be read')
        return params
    if method in _APP:
        cap, allowed = _APP[method]
        _need_any(principal, (cap,), method)
        if set(params) - allowed:
            raise DeviceAccessDenied('unsupported device parameters')
        sid = params.get('session_id')
        if sid:
            session = sessions.get(sid) if isinstance(sid, str) else None
            if session is None:
                raise DeviceAccessDenied('session unavailable')
            ensure_session_owned(transport, session, launch_home=launch_home)
        return params
    return None


def authorize_request(transport, method, params, sessions, *, launch_home=None):
    principal = device_principal(transport)
    if principal is None:
        return params
    if method in _OPEN:
        if set(params) - _OPEN[method]:
            raise DeviceAccessDenied('unsupported device parameters')
        return params
    if method in _DESK:
        if 'control_drive' not in principal.capabilities:
            from tui_gateway import desk_drive
            desk_drive.release_device(f'dev:{principal.device_id}', 'not granted')
            _refuse(principal, 'control_drive', method)
        if set(params) - _DESK[method]:
            raise DeviceAccessDenied('unsupported device parameters')
        if method != 'desk.input':
            _audit(principal, 'control_drive', method, 'allow')
        return params
    early = _authorize_app_surface(transport, principal, method, params, sessions, launch_home)
    if early is not None:
        return early
    rule = _RULES.get(method)
    crew_cap = None
    if method in _CREW_READ:
        rule, crew_cap = ('crew_read', _CREW_READ[method]), 'crew_read'
    elif method in _CREW_ACT:
        rule, crew_cap = ('crew_act', _CREW_ACT[method]), 'crew_act'
    elif method in _CREW_SESSION_PARAMS and rule is not None:
        # Same chat/history rule as today; crew_act only widens WHICH profile.
        requested = params.get('profile')
        if requested is not None and requested != principal.profile:
            if 'crew_act' not in principal.capabilities:
                _refuse(principal, 'crew_act', method, 'device profile mismatch')
    if rule is None:
        raise DeviceAccessDenied('operation is not permitted for this device')
    if rule[0] not in principal.capabilities:
        _refuse(principal, rule[0], method)
    if rule[1] is not None and set(params) - rule[1]:
        raise DeviceAccessDenied('unsupported device parameters')
    if crew_cap:
        if method == 'profiles.configure' and (params.get('name') != 'default' or set(params) - {'name', 'ui_meta', 'ui_meta_expected_revisions'}):
            raise DeviceAccessDenied('only crew rooms may be saved')
        if method == 'session.list':
            requested = params.get('profile')
            if requested is not None and requested not in crew_profile_names() and requested != principal.profile:
                raise DeviceAccessDenied('device profile mismatch')
        _audit(principal, crew_cap, method, 'allow')
        return params
    if method == 'config.set':
        value = params.get('value')
        if (params.get('key') != 'model' or not isinstance(value, str)
                or not value or any(ch.isspace() for ch in value) or value.startswith('-')):
            raise DeviceAccessDenied('only session model selection is permitted')
    requested = params.get('profile')
    if requested is not None and requested != principal.profile:
        # Only reachable with crew_act (checked above): the profile must be a crew member.
        effective_profile(principal, requested)
        _audit(principal, 'crew_act', method, 'allow')
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
        scoped['profile'] = effective_profile(principal, requested)
    return scoped
