"""Regression tests for staged updater mutation admission."""
from types import SimpleNamespace
from contextlib import contextmanager
import subprocess

import pytest


@pytest.mark.parametrize('outcome', ['refuse', 'fetch_failure', 'admitted'])
def test_staged_mutation_order(tmp_path, monkeypatch, outcome):
    from clover_cli import main, update_cmd, _migration_target as target
    from clover_cli import config, update_contract, update_preflight, update_delivery_guard, gitlock, update_inventory
    from clover_cli import macos_update_launchd

    home = tmp_path / 'home'
    home.mkdir()
    config_file = home / 'config.yaml'
    config_file.write_bytes(b'config_version: 1\nupdates: {}\n')
    original_config = config_file.read_bytes()
    root = tmp_path / 'target'
    root.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
    git('init', '-qb', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (root / 'tracked').write_text('fixture')
    git('add', 'tracked')
    git('commit', '-qm', 'fixture')
    old_commit = git('rev-parse', 'HEAD').stdout.decode().strip()
    stage = tmp_path / 'stage'
    subprocess.run(['git', 'clone', '-q', str(root), str(stage)], check=True)
    subprocess.run(['git', '-C', str(stage), 'config', 'user.name', 'Fixture'], check=True)
    subprocess.run(['git', '-C', str(stage), 'config', 'user.email', 'fixture@example.invalid'], check=True)
    (stage / 'candidate').write_text('new candidate')
    subprocess.run(['git', '-C', str(stage), 'add', 'candidate'], check=True)
    subprocess.run(['git', '-C', str(stage), 'commit', '-qm', 'candidate'], check=True)
    candidate = subprocess.check_output(['git', '-C', str(stage), 'rev-parse', 'HEAD'], text=True).strip()
    git('remote', 'add', 'origin', str(tmp_path / 'missing-origin' if outcome == 'fetch_failure' else stage))
    index = (root / '.git/index').read_bytes()
    refs = git('show-ref').stdout
    index_lock = root / '.git/index.lock'
    index_lock.write_bytes(b'fixture lock')
    monkeypatch.setenv('CLOVER_HOME', str(home))
    monkeypatch.setattr(main, 'PROJECT_ROOT', root)
    monkeypatch.setattr(target, '_target', root)
    monkeypatch.setattr(target, '_identities', (stage, root, candidate, old_commit))
    monkeypatch.setattr(target, '_prepared_commit', None)
    monkeypatch.setattr(target, 'revalidate_target', lambda: None)
    monkeypatch.setattr(config, 'is_managed', lambda: False)
    monkeypatch.setattr(update_contract, 'evaluate_update_admission', lambda *a: None)
    monkeypatch.setattr(update_preflight, 'gate_cli_update', lambda *a, **k: None)
    monkeypatch.setattr(update_delivery_guard, 'require_delivery_safe_update', lambda *a, **k: None)
    monkeypatch.setattr(main, '_capture_active_lazy_features', lambda: {})
    monkeypatch.setattr(main, '_capture_active_tool_dependencies', lambda: {})
    monkeypatch.setattr(update_inventory, 'collect_runtime_inventory', lambda: SimpleNamespace(runtimes=[]))
    monkeypatch.setattr(update_inventory, 'record_plan_in_receipt', lambda *a: None)
    monkeypatch.setattr(main, '_is_windows', lambda: False)
    calls = []
    migrations = []
    monkeypatch.setattr(config, 'migrate_config', lambda *a, **k: migrations.append(True))
    monkeypatch.setattr(config, 'check_config_version', lambda **k: (1, 2))
    @contextmanager
    def window(path):
        calls.append(('admission', path))
        if outcome == 'refuse':
            raise RuntimeError('fixture Darwin admission refusal')
        try:
            yield SimpleNamespace(restored={})
        finally:
            calls.append(('restore', path))
    monkeypatch.setattr(macos_update_launchd, 'darwin_workspace_window', window)
    real_fetch = target.prepare_staged_fetch
    def fetch(branch):
        calls.append(('fetch', branch))
        assert real_fetch(branch) == candidate
        assert target.prepared_staged_commit() == candidate
    monkeypatch.setattr(target, 'prepare_staged_fetch', fetch)
    monkeypatch.setattr(gitlock, 'release_dead_index_lock', lambda path: calls.append(('release', path)))
    def stop_at_backup(args):
        calls.append(('backup', root))
        config.load_config()
        raise RuntimeError('fixture backup stop')
    monkeypatch.setattr(main, '_run_pre_update_backup', stop_at_backup)
    expected_error = ValueError if outcome == 'fetch_failure' else RuntimeError
    expected_message = 'staged fetch failed' if outcome == 'fetch_failure' else 'fixture'
    with pytest.raises(expected_error, match=expected_message):
        main.cmd_update(SimpleNamespace(branch=None, yes=True, force=False, gateway=False))
    if outcome == 'refuse':
        assert calls == [('admission', root)]
    elif outcome == 'fetch_failure':
        assert calls == [('admission', root), ('fetch', 'main'), ('restore', root)]
    else:
        assert calls == [('admission', root), ('fetch', 'main'), ('release', root), ('backup', root), ('restore', root)]
    assert migrations == ([True] if outcome == 'admitted' else [])
    assert config_file.read_bytes() == original_config
    assert index_lock.read_bytes() == b'fixture lock'
    assert (root / '.git/index').read_bytes() == index
    if outcome != 'admitted':
        assert git('show-ref').stdout == refs
    else:
        assert git('rev-parse', 'refs/remotes/origin/main').stdout.decode().strip() == candidate
