"""Updater drain boundary; real marker files, no process signals."""
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest


def test_owned_drain_waits_for_reply_and_cleans_own_marker(tmp_path, monkeypatch):
    from clover_cli import macos_update_lifecycle as lifecycle
    from gateway.drain_control import drain_request_path
    ticks = []
    clock = [0.0]
    def status():
        ticks.append('status')
        return {
            'kind': 'clover-gateway', 'pid': 123, 'start_time': 'birth-123',
            'clover_home': str(tmp_path), 'gateway_state': 'draining', 'update_restart_capability': 'reply-carriers-through-ack-v1',
            'active_agents': 1 if len(ticks) < 3 else 0,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
    def sleep(seconds):
        assert drain_request_path(tmp_path).exists()
        clock[0] += seconds
    monkeypatch.setattr(lifecycle, '_read_status', lambda home: status())
    monkeypatch.setattr(lifecycle, '_birth', lambda pid: 'birth-123')
    monkeypatch.setattr(lifecycle.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(lifecycle.time, 'sleep', sleep)
    with lifecycle.owned_external_drain(tmp_path, 123, 'birth-123', timeout=2):
        assert len(ticks) >= 3
        assert drain_request_path(tmp_path).exists()
    assert not drain_request_path(tmp_path).exists()


@pytest.mark.parametrize('change', [
    {'pid': 999}, {'start_time': 'reused'}, {'clover_home': '/not-this-profile'},
    {'kind': 'not-a-gateway'},
])
def test_wrong_status_owner_refuses_and_releases_only_own_drain(tmp_path, monkeypatch, change):
    from clover_cli import macos_update_lifecycle as lifecycle
    from gateway.drain_control import drain_request_path
    state = {'kind': 'clover-gateway', 'pid': 123, 'start_time': 'birth',
             'clover_home': str(tmp_path), 'gateway_state': 'draining', 'update_restart_capability': 'reply-carriers-through-ack-v1',
             'active_agents': 0, 'updated_at': datetime.now(timezone.utc).isoformat()}
    state.update(change)
    monkeypatch.setattr(lifecycle, '_birth', lambda pid: 'birth')
    monkeypatch.setattr(lifecycle, '_read_status', lambda home: state)
    with pytest.raises(lifecycle.LifecycleRefused):
        with lifecycle.owned_external_drain(tmp_path, 123, 'birth', timeout=0):
            pytest.fail('mutation admitted with foreign status')
    assert not drain_request_path(tmp_path).exists()


@pytest.mark.parametrize('change', [
    {'gateway_state': 'running'}, {'active_agents': 1}, {'active_agents': None},
    {'active_agents': False}, {'active_agents': '0'},
    {'updated_at': '2000-01-01T00:00:00+00:00'},
])
def test_missing_fresh_zero_work_ack_does_not_admit_mutation(tmp_path, monkeypatch, change):
    from clover_cli import macos_update_lifecycle as lifecycle
    from gateway.drain_control import drain_request_path
    def status(home):
        state = {'kind': 'clover-gateway', 'pid': 123, 'start_time': 'birth',
                 'clover_home': str(tmp_path), 'gateway_state': 'draining', 'update_restart_capability': 'reply-carriers-through-ack-v1',
                 'active_agents': 0, 'updated_at': datetime.now(timezone.utc).isoformat()}
        state.update(change)
        return state
    monkeypatch.setattr(lifecycle, '_birth', lambda pid: 'birth')
    monkeypatch.setattr(lifecycle, '_read_status', status)
    with pytest.raises(lifecycle.LifecycleRefused, match='deadline'):
        with lifecycle.owned_external_drain(tmp_path, 123, 'birth', timeout=0):
            pytest.fail('mutation admitted before work drained')
    assert not drain_request_path(tmp_path).exists()


def test_foreign_drain_preserved_byte_for_byte(tmp_path, monkeypatch):
    from clover_cli import macos_update_lifecycle as lifecycle
    from gateway.drain_control import drain_request_path
    monkeypatch.setattr(lifecycle, '_birth', lambda pid: 'birth')
    path = drain_request_path(tmp_path)
    path.write_bytes(b'{"principal": "operator"}')
    with pytest.raises(lifecycle.LifecycleRefused, match='already owns'):
        with lifecycle.owned_external_drain(tmp_path, 123, 'birth'):
            pytest.fail('foreign drain stolen')
    assert path.read_bytes() == b'{"principal": "operator"}'


def test_quiescent_window_restores_once_after_mutation_failure(tmp_path, monkeypatch):
    from clover_cli import macos_update_lifecycle as lifecycle
    from contextlib import contextmanager
    from types import SimpleNamespace
    events = []
    service = SimpleNamespace(home=tmp_path, pid=123, birth='birth')
    @contextmanager
    def drain(*args, **kwargs):
        events.append('drain')
        try:
            yield
        finally:
            events.append('release-drain')
    class Operations:
        def validate(self, item):
            assert item is service
            events.append('validate')
        def capture_builders(self, items):
            assert items == [service]
            events.append('capture')
            return ['owned-child']
        def unload(self, item):
            events.append('unload')
        def wait_quiescent(self, items, children, timeout):
            assert children == ['owned-child']
            events.append('wait-exit-and-builders')
        def restore(self, item):
            events.append('restore-supervised')
    monkeypatch.setattr(lifecycle, 'owned_external_drain', drain)
    with pytest.raises(RuntimeError, match='npm failed'):
        with lifecycle.quiescent_services([service], Operations()):
            events.append('npm')
            raise RuntimeError('npm failed')
    assert events == ['validate', 'drain', 'capture', 'validate', 'unload',
                      'wait-exit-and-builders', 'npm', 'release-drain',
                      'restore-supervised']


def test_pid_reuse_while_waiting_never_signals_or_admits(tmp_path, monkeypatch):
    from clover_cli import macos_update_lifecycle as lifecycle
    from gateway.drain_control import drain_request_path
    births = iter(['birth', 'different-process'])
    monkeypatch.setattr(lifecycle, '_birth', lambda pid: next(births))
    with pytest.raises(lifecycle.LifecycleRefused, match='identity changed'):
        with lifecycle.owned_external_drain(tmp_path, 123, 'birth'):
            pytest.fail('reused PID admitted')
    assert not drain_request_path(tmp_path).exists()


def test_partial_unload_failure_restores_attempted_services_in_reverse():
    from clover_cli import macos_update_lifecycle as lifecycle
    from types import SimpleNamespace
    events = []
    services = [SimpleNamespace(pid=None, name=name) for name in ("one", "two")]
    class Operations:
        def validate(self, service): pass
        def capture_builders(self, items): return []
        def unload(self, service):
            events.append(("unload", service.name))
            if service.name == "two":
                raise RuntimeError("bootout timed out")
        def wait_quiescent(self, *args): pytest.fail("must not mutate")
        def restore(self, service): events.append(("restore", service.name))
    with pytest.raises(RuntimeError, match="bootout timed out"):
        with lifecycle.quiescent_services(services, Operations()):
            pytest.fail("mutation admitted")
    assert events == [("unload", "one"), ("unload", "two"),
                      ("restore", "two"), ("restore", "one")]


def test_restoration_failure_is_loud_and_does_not_skip_other_restores():
    from clover_cli import macos_update_lifecycle as lifecycle
    from types import SimpleNamespace
    events = []
    services = [SimpleNamespace(pid=None, name=name) for name in ("one", "two")]
    class Operations:
        def validate(self, service): pass
        def capture_builders(self, items): return []
        def unload(self, service): pass
        def wait_quiescent(self, *args): pass
        def restore(self, service):
            events.append(service.name)
            if service.name == "two": raise RuntimeError("bootstrap failed")
    with pytest.raises(lifecycle.LifecycleRefused, match="bootstrap failed"):
        with lifecycle.quiescent_services(services, Operations()): pass
    assert events == ["two", "one"]
