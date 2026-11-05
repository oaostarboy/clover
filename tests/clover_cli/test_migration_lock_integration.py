"""Mutation admission must revalidate under the updater lock."""
import pytest


def test_cmd_update_revalidates_binding_after_lock(monkeypatch):
    from clover_cli import main, _migration_target
    from clover_cli.update_lock import UpdateLock
    from clover_cli import update_contract
    from types import SimpleNamespace
    events = []
    monkeypatch.setattr(main, '_install_hangup_protection', lambda **kw: None)
    monkeypatch.setattr(main, '_finalize_update_output', lambda *a: None)
    monkeypatch.setattr(UpdateLock, 'acquire', lambda self: events.append('lock') or True)
    monkeypatch.setattr(UpdateLock, 'release', lambda self: events.append('release'))
    monkeypatch.setattr(update_contract, 'evaluate_update_admission', lambda root: None)
    import clover_cli.config as config
    monkeypatch.setattr(config, 'is_managed', lambda: False)
    def unexpected_body(*args, **kwargs):
        raise AssertionError('update body reached before revalidation')
    monkeypatch.setattr(main, '_cmd_update_impl', unexpected_body)
    def refused():
        events.append('validate')
        raise ValueError('identity changed')
    monkeypatch.setattr(_migration_target, 'revalidate_target', refused, raising=False)
    with pytest.raises(ValueError, match='identity changed'):
        main.cmd_update(SimpleNamespace(gateway=True))
    assert events == ['lock', 'validate', 'release']
