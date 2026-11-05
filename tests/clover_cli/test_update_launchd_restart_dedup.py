"""Restored launchd services must not be restarted a second time."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import clover_cli.gateway as gateway
import clover_cli.update_cmd as update
import clover_cli.macos_update_launchd as launchd


@pytest.fixture(autouse=True)
def receipts(monkeypatch):
    monkeypatch.setattr(update, '_macos_restored_services', {}, raising=False)


def test_fleet_skips_verified_current_and_sibling(monkeypatch):
    monkeypatch.setattr(gateway, 'get_launchd_label', lambda: 'current')
    monkeypatch.setattr(gateway, 'launchd_gateway_labels_for_install', lambda: ['current', 'sibling'])
    monkeypatch.setattr(update, '_macos_service_already_restored', lambda label: True, raising=False)
    monkeypatch.setattr(update, '_restart_launchd_gateway_after_update', lambda **kw: pytest.fail('current restarted'))
    monkeypatch.setattr(gateway, '_locate_launchd_gateway_service', lambda label: pytest.fail('sibling probed'))
    restarted, failed = [], []
    update._restart_macos_launchd_gateways(restarted, failed, 5)
    assert restarted == ['current', 'sibling']
    assert failed == []


def test_unverified_services_follow_existing_restart_paths(monkeypatch):
    monkeypatch.setattr(gateway, 'get_launchd_label', lambda: 'current')
    monkeypatch.setattr(gateway, 'launchd_gateway_labels_for_install', lambda: ['current', 'sibling'])
    calls = []
    monkeypatch.setattr(update, '_restart_launchd_gateway_after_update', lambda **kw: (calls.append(('current', kw)) or (['current'], [])))
    monkeypatch.setattr(gateway, '_locate_launchd_gateway_service', lambda label: ('gui/501', 42))
    monkeypatch.setattr(gateway, '_graceful_restart_via_sigusr1', lambda *a, **kw: False)
    monkeypatch.setattr(gateway, '_launchd_kickstart', lambda label, domain: calls.append((label, domain)))
    monkeypatch.setattr(gateway, '_wait_for_launchd_service_pid', lambda *a, **kw: True)
    restarted, failed = [], []
    update._restart_macos_launchd_gateways(restarted, failed, 5)
    assert calls == [('current', {'supervision_verify': True}), ('sibling', 'gui/501')]
    assert restarted == ['current', 'sibling'] and not failed


def _service(tmp_path, **changes):
    from dataclasses import replace
    s = launchd.Service(tmp_path, tmp_path / 'home', 'current', 'gui/501',
                        tmp_path / 'service.plist', b'plist', True, 22, 33, 44, 55)
    return replace(s, **changes)


def test_exact_receipt_is_validated(monkeypatch, tmp_path):
    service = _service(tmp_path)
    monkeypatch.setattr(update._m(), 'PROJECT_ROOT', tmp_path)
    update._macos_restored_services['current'] = service
    seen = []
    monkeypatch.setattr(launchd.LaunchdOperations, 'validate', lambda self, value: seen.append(value))
    assert update._macos_service_already_restored('current')
    assert seen == [service]


@pytest.mark.parametrize('changes', [dict(pid=None), dict(birth=None), dict(supervisor_pid=None),
                                      dict(supervisor_birth=None), dict(domain=None), dict(loaded=False)])
def test_incomplete_receipt_never_skips(monkeypatch, tmp_path, changes):
    monkeypatch.setattr(update._m(), 'PROJECT_ROOT', tmp_path)
    update._macos_restored_services['current'] = _service(tmp_path, **changes)
    assert not update._macos_service_already_restored('current')


def test_other_install_and_failed_validation_never_skip(monkeypatch, tmp_path):
    service = _service(tmp_path)
    update._macos_restored_services['current'] = service
    monkeypatch.setattr(update._m(), 'PROJECT_ROOT', tmp_path / 'other')
    assert not update._macos_service_already_restored('current')
    monkeypatch.setattr(update._m(), 'PROJECT_ROOT', tmp_path)
    def refuse(self, value):
        raise launchd.LifecycleRefused('changed identity')
    monkeypatch.setattr(launchd.LaunchdOperations, 'validate', refuse)
    assert not update._macos_service_already_restored('current')


@pytest.mark.parametrize('failure', [False, True])
def test_refresh_records_only_after_exit_and_preserves_node_failure(monkeypatch, tmp_path, failure):
    service = _service(tmp_path)
    operations = SimpleNamespace(restored={})
    @contextmanager
    def window(root):
        yield operations
        assert not update._macos_restored_services
        operations.restored[service] = service
    monkeypatch.setattr(launchd, 'darwin_workspace_window', window)
    monkeypatch.setattr(update._m(), 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(update, '_update_node_dependencies', lambda: ['web'] if failure else [])
    monkeypatch.setattr(update._m(), '_build_web_ui', lambda root: pytest.fail('build on failure') if failure else None)
    monkeypatch.setattr(update, '_rebuild_desktop_after_update', lambda *a, **kw: True)
    assert update._refresh_node_artifacts(False, skip_build_on_node_failure=True) == ((['web'], False) if failure else ([], True))
    assert update._macos_restored_services == {'current': service}


def test_refresh_does_not_record_failed_restoration(monkeypatch, tmp_path):
    service = _service(tmp_path)
    @contextmanager
    def window(root):
        yield SimpleNamespace(restored={service: service})
        raise RuntimeError('restore failed')
    monkeypatch.setattr(launchd, 'darwin_workspace_window', window)
    monkeypatch.setattr(update, '_update_node_dependencies', lambda: ['web'])
    with pytest.raises(RuntimeError):
        update._refresh_node_artifacts(False, skip_build_on_node_failure=True)
    assert update._macos_restored_services == {}


def test_refresh_does_not_record_failed_mutation(monkeypatch):
    @contextmanager
    def window(root):
        yield SimpleNamespace(restored={})
    monkeypatch.setattr(launchd, 'darwin_workspace_window', window)
    def fail():
        raise RuntimeError('mutation failed')
    monkeypatch.setattr(update, '_update_node_dependencies', fail)
    with pytest.raises(RuntimeError, match='mutation failed'):
        update._refresh_node_artifacts(False)
    assert update._macos_restored_services == {}


def test_new_update_clears_receipts_before_guard(monkeypatch):
    import clover_cli.update_delivery_guard as guard
    update._macos_restored_services['current'] = object()
    def stop():
        assert not update._macos_restored_services
        raise RuntimeError('stop')
    monkeypatch.setattr(guard, 'require_delivery_safe_update', stop)
    with pytest.raises(RuntimeError, match='stop'):
        update._cmd_update_impl(SimpleNamespace(), False)
