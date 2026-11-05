"""Scope lifetime tests; context spy, not native launchd evidence."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from clover_cli import macos_update_launchd as launchd
from clover_cli import macos_update_transaction as tx
from clover_cli.macos_update_lifecycle import LifecycleRefused


@pytest.fixture
def window(monkeypatch):
    events = []
    @contextmanager
    def spy(root):
        events.append('pause')
        try:
            yield SimpleNamespace(restored={})
        finally:
            events.append('restore')
    monkeypatch.setattr(launchd, 'darwin_workspace_window', spy)
    return events


def test_nested_fallback_and_build_share_one_window(tmp_path, window):
    @tx.source_update_scope
    def zip_fallback():
        tx.begin_source_mutation(tmp_path)
        window.append('zip-swap')
        tx.finish_source_mutation()
        window.append('receipt')
    @tx.source_update_scope
    def git():
        tx.begin_source_mutation(tmp_path)
        window.append('git')
        zip_fallback()
    git()
    assert window == ['pause', 'git', 'zip-swap', 'restore', 'receipt']
    assert tx._current.get() is None


@pytest.mark.parametrize('failure', [RuntimeError('git failed'), SystemExit(1), KeyboardInterrupt()])
def test_source_failure_restores_once_and_releases_owner(tmp_path, window, failure):
    @tx.source_update_scope
    def update():
        tx.begin_source_mutation(tmp_path)
        raise failure
    with pytest.raises(type(failure)):
        update()
    assert window == ['pause', 'restore']
    assert tx._current.get() is None


def test_read_only_scope_never_pauses(window):
    @tx.source_update_scope
    def check():
        return 'up to date'
    assert check() == 'up to date'
    assert window == []


def test_nested_foreign_root_refuses_then_restores_original(tmp_path, window):
    @tx.source_update_scope
    def update():
        tx.begin_source_mutation(tmp_path)
        tx.begin_source_mutation(tmp_path / 'foreign')
    with pytest.raises(LifecycleRefused, match='ownership'):
        update()
    assert window == ['pause', 'restore']


@pytest.mark.parametrize('retry', [True, False])
def test_failed_restore_cannot_be_reentered_or_reported_success(tmp_path, monkeypatch, retry):
    events = []
    @contextmanager
    def failing(root):
        events.append('pause')
        try:
            yield None
        finally:
            events.append('restore')
            raise LifecycleRefused('restoration failed')
    monkeypatch.setattr(launchd, 'darwin_workspace_window', failing)
    @tx.source_update_scope
    def update():
        tx.begin_source_mutation(tmp_path)
        with pytest.raises(LifecycleRefused, match='restoration'):
            tx.finish_source_mutation()
        if retry:
            tx.begin_source_mutation(tmp_path)
        return True  # A broad caller catch must not turn failed cleanup into success.
    with pytest.raises(LifecycleRefused, match='earlier'):
        update()
    assert events == ['pause', 'restore']
    assert tx._current.get() is None
