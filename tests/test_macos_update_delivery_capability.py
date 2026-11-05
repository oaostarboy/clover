"""Caller-bound refusal of idle legacy runners with unknown reply delivery.

OS/service discovery is isolated; the production caller and drain run intact.
This does not assert live legacy transport behaviour.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from clover_cli import macos_update_lifecycle as lifecycle
from clover_cli import macos_update_launchd as launchd
from clover_cli import update_cmd
from clover_cli.update_delivery_guard import CAPABILITY
from gateway.drain_control import drain_request_path


@pytest.mark.parametrize('capability', [None, '', 'unknown-contract', False])
def test_repair_refuses_unknown_delivery_before_unload_or_npm(tmp_path, monkeypatch, capability):
    events = []
    service = SimpleNamespace(home=tmp_path, pid=123, birth='birth')
    state = dict(kind='clover-gateway', pid=123, start_time='birth',
                 clover_home=str(tmp_path), gateway_state='draining', active_agents=0)
    if capability is not None:
        state['update_restart_capability'] = capability
    def status(home):
        return dict(state, updated_at=datetime.now(timezone.utc).isoformat())
    operations = SimpleNamespace(
        validate=lambda s: None,
        capture_builders=lambda s: [],
        unload=lambda s: events.append('unload'),
        wait_quiescent=lambda *args: None,
        restore=lambda s: events.append('restore'),
    )
    @contextmanager
    def window(root):
        with lifecycle.quiescent_services([service], operations, timeout=0):
            yield None
    monkeypatch.setattr(launchd, 'darwin_workspace_window', window)
    monkeypatch.setattr(lifecycle, '_birth', lambda pid: 'birth')
    monkeypatch.setattr(lifecycle, '_read_status', status)
    monkeypatch.setattr(update_cmd, '_desktop_app_present', lambda *a: False)
    monkeypatch.setattr(update_cmd, '_update_node_dependencies', lambda: events.append('npm') or [])
    monkeypatch.setattr(update_cmd._m(), '_build_web_ui', lambda *a: None)
    monkeypatch.setattr(update_cmd, '_rebuild_desktop_after_update', lambda *a, **kw: True)
    monkeypatch.setattr(update_cmd, '_check_and_apply_config_migration', lambda **kw: None)
    with pytest.raises(lifecycle.LifecycleRefused, match='delivery'):
        update_cmd._repair_node_deps_on_current_checkout(lambda *a: events.append('success'))
    assert events == []
    assert not drain_request_path(tmp_path).exists()


def test_capable_runner_waits_for_delivery_ack(tmp_path, monkeypatch):
    pending = [True]
    checks = []
    def status(home):
        checks.append(pending[0])
        return dict(kind='clover-gateway', pid=123, start_time='birth',
                    clover_home=str(tmp_path), gateway_state='draining',
                    active_agents=int(pending[0]), update_restart_capability=CAPABILITY,
                    updated_at=datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(lifecycle, '_birth', lambda pid: 'birth')
    monkeypatch.setattr(lifecycle, '_read_status', status)
    monkeypatch.setattr(lifecycle.time, 'sleep', lambda seconds: pending.__setitem__(0, False))
    with lifecycle.owned_external_drain(tmp_path, 123, 'birth', timeout=1):
        assert checks == [True, False]
        assert not pending[0]
