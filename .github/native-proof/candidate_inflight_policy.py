"""Fail-closed acceptance policy for in-flight native drivers."""


def require_first_update_progress(old, current):
    assert current != old, 'first update did not advance the source; manual retry cannot be an automatic PASS'


def require_guard_status(actual, expected, held_state=None):
    assert expected in {'legacy-proceed', 'capable'}
    if actual is None and expected == 'capable':
        state = held_state or {}
        assert state.get('update_restart_capability') == 'reply-carriers-through-ack-v1'
        assert state.get('gateway_state') == 'draining' and state.get('restart_requested') is True
        assert state.get('active_agents', 0) >= 1
        return
    assert actual == expected, (actual, expected)


def require_config_preserved(before, after, *, _root=True):
    if isinstance(before, dict):
        assert isinstance(after, dict)
        for key, value in before.items():
            assert key in after, f'missing config key {key}'
            if _root and key == '_config_version':
                assert type(value) is int and type(after[key]) is int and after[key] >= value
            else:
                require_config_preserved(value, after[key], _root=False)
    else:
        assert type(before) is type(after) and before == after, (before, after)


def desktop_build_processes(processes, source):
    source = str(source).replace('\\', '/').rstrip('/').lower() + '/'
    matches = []
    for process in processes:
        argv = process.get('cmdline') or []
        if not argv:
            continue
        exe = str(argv[0]).replace('\\', '/').lower()
        if exe.startswith(source) and 'desktop' in argv and '--build-only' in argv:
            matches.append(process['pid'])
    return matches
