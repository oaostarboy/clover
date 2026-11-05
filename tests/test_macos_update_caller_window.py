from contextlib import contextmanager
import pytest
from clover_cli import update_cmd
from clover_cli import macos_update_launchd


def test_repair_mutates_only_inside_window_and_completes_after_restore(monkeypatch):
    events = []
    @contextmanager
    def window(root):
        events.append('enter')
        try:
            yield None
        finally:
            events.append('restore')
    monkeypatch.setattr(macos_update_launchd, 'darwin_workspace_window', window)
    monkeypatch.setattr(update_cmd, '_update_node_dependencies', lambda: events.append('npm') or [])
    monkeypatch.setattr(update_cmd._m(), '_build_web_ui', lambda *a: events.append('web'))
    monkeypatch.setattr(update_cmd, '_desktop_app_present', lambda *a: False)
    monkeypatch.setattr(update_cmd, '_rebuild_desktop_after_update', lambda *a, **kw: events.append('desktop') or True)
    monkeypatch.setattr(update_cmd, '_check_and_apply_config_migration', lambda **kw: None)
    assert update_cmd._repair_node_deps_on_current_checkout(lambda _: events.append('complete'))
    assert events == ['enter', 'npm', 'web', 'desktop', 'restore', 'complete']


@pytest.mark.parametrize('stage', ['npm', 'web', 'restore'])
def test_repair_exception_does_not_report_success(monkeypatch, stage):
    events = []
    @contextmanager
    def window(root):
        try:
            yield None
        finally:
            events.append('restore')
            if stage == 'restore':
                raise RuntimeError('restore failed')
    def call(name, value=None):
        events.append(name)
        if stage == name:
            raise RuntimeError(name + ' failed')
        return value
    monkeypatch.setattr(macos_update_launchd, 'darwin_workspace_window', window)
    monkeypatch.setattr(update_cmd, '_update_node_dependencies', lambda: call('npm', []))
    monkeypatch.setattr(update_cmd._m(), '_build_web_ui', lambda *a: call('web'))
    monkeypatch.setattr(update_cmd, '_desktop_app_present', lambda *a: False)
    monkeypatch.setattr(update_cmd, '_rebuild_desktop_after_update', lambda *a, **kw: call('desktop', True))
    with pytest.raises(RuntimeError):
        update_cmd._repair_node_deps_on_current_checkout(lambda _: events.append('complete'))
    assert 'restore' in events
    assert 'complete' not in events


def test_helper_guard_refusal_prevents_all_mutations(monkeypatch):
    events = []
    @contextmanager
    def window(root):
        raise macos_update_launchd.LifecycleRefused('refused')
        yield
    monkeypatch.setattr(macos_update_launchd, 'darwin_workspace_window', window)
    monkeypatch.setattr(update_cmd, '_update_node_dependencies', lambda: events.append('npm'))
    monkeypatch.setattr(update_cmd._m(), '_build_web_ui', lambda *a: events.append('web'))
    monkeypatch.setattr(update_cmd, '_rebuild_desktop_after_update', lambda *a, **kw: events.append('desktop'))
    with pytest.raises(macos_update_launchd.LifecycleRefused):
        update_cmd._refresh_node_artifacts(False)
    assert events == []


def test_helper_restore_failure_prevents_returned_success(monkeypatch):
    events = []
    @contextmanager
    def window(root):
        yield
        events.append('restore')
        raise RuntimeError('restore failed')
    monkeypatch.setattr(macos_update_launchd, 'darwin_workspace_window', window)
    monkeypatch.setattr(update_cmd, '_update_node_dependencies', lambda: events.append('npm') or [])
    monkeypatch.setattr(update_cmd._m(), '_build_web_ui', lambda *a: events.append('web'))
    monkeypatch.setattr(update_cmd, '_rebuild_desktop_after_update', lambda *a, **kw: events.append('desktop') or True)
    with pytest.raises(RuntimeError, match='restore failed'):
        update_cmd._refresh_node_artifacts(False)
    assert events == ['npm', 'web', 'desktop', 'restore']
