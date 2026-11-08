import pytest

from tools.computer_use.cua_backend import CuaDriverBackend


class StrictSession:
    """Current driver: structured rows are opt-in, clicks accept tokens only."""
    def __init__(self, legacy=False):
        self.legacy = legacy
        self.calls = []
        self.generation = 0
        self.fail_capture = False
        self.empty = False

    def supports_capability(self, capability, tool=None):
        # Exercise schema-only discovery, not an assumed named capability.
        return False

    def supports_input_property(self, tool, prop):
        if tool == 'get_window_state':
            return prop in {'pid', 'window_id', 'session'} or (prop == 'tree_format' and not self.legacy)
        return prop in {'pid', 'window_id', 'button', 'session'} or prop == ('element_index' if self.legacy else 'element_token')

    def call_tool(self, tool, args):
        self.calls.append((tool, dict(args)))
        if tool == 'get_window_state':
            if self.fail_capture:
                raise RuntimeError('fixture capture failed')
            self.generation += 1
            row = {'element_index': 0, 'role': 'button', 'label': 'Increment',
                   'element_token': f's{self.generation:08x}:0'}
            structured = {'elements': [] if self.empty else [row]}
            if args.get('tree_format') != 'elements':
                structured = {}  # Real default driver response is markdown.
            return {'data': 'Window\n\n[0] button "Increment"', 'images': [], 'isError': False, 'structuredContent': structured}
        assert tool == 'click'
        permitted = {'pid', 'window_id', 'button', 'session', 'element_index' if self.legacy else 'element_token'}
        assert set(args) <= permitted
        if not self.legacy:
            assert args['element_token'] == f's{self.generation:08x}:0'
        return {'data': 'ok', 'isError': False, 'structuredContent': {}}


def backend(session):
    result = CuaDriverBackend()
    result._session = session
    return result


def test_real_capture_contract_preserves_zero_index_and_token():
    session = StrictSession()
    subject = backend(session)
    capture = subject.capture(mode='ax', pid=123, window_id=456)
    assert capture.elements[0].index == 0
    assert capture.elements[0].element_token == 's00000001:0'
    assert subject.click(element=capture.elements[0].index).ok
    assert session.calls[-1][1]['element_token'] == 's00000001:0'
    assert 'element_index' not in session.calls[-1][1]


def test_fresh_capture_replaces_old_token_and_target():
    session = StrictSession()
    subject = backend(session)
    subject.capture(mode='ax', pid=123, window_id=456)
    subject.capture(mode='ax', pid=124, window_id=457)
    assert subject.click(element=0).ok
    assert session.calls[-1][1]['element_token'] == 's00000002:0'
    assert session.calls[-1][1]['pid'] == 124
    assert session.calls[-1][1]['window_id'] == 457


def test_failed_capture_disarms_previous_target():
    session = StrictSession()
    subject = backend(session)
    subject.capture(mode='ax', pid=123, window_id=456)
    session.fail_capture = True
    with pytest.raises(RuntimeError):
        subject.capture(mode='ax', pid=124, window_id=457)
    count = len(session.calls)
    assert not subject.click(element=0).ok
    assert len(session.calls) == count


def test_unknown_element_fails_without_native_input():
    session = StrictSession()
    subject = backend(session)
    subject.capture(mode='ax', pid=123, window_id=456)
    count = len(session.calls)
    result = subject.click(element=999)
    assert not result.ok
    assert result.code == 'element_snapshot_missing'
    assert len(session.calls) == count


def test_legacy_driver_keeps_index_without_new_capture_argument():
    session = StrictSession(legacy=True)
    subject = backend(session)
    subject._active_pid, subject._active_window_id = 123, 456
    assert subject.click(element=0).ok
    assert session.calls[-1][1]['element_index'] == 0
    assert 'element_token' not in session.calls[-1][1]


def test_backend_sessions_do_not_share_tokens():
    first = backend(StrictSession())
    second = backend(StrictSession())
    first.capture(mode='ax', pid=123, window_id=456)
    second._active_pid, second._active_window_id = 123, 456
    assert not second.click(element=0).ok
    assert first.click(element=0).ok
