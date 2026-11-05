"""Regression coverage for staged migration admission."""
import pytest


def test_staged_delivery_guard_refuses_identified_legacy(monkeypatch):
    from clover_cli import update_delivery_guard as guard
    monkeypatch.setattr(guard, '_unsafe_gateways', lambda: ['legacy: fixture: identified legacy runtime'])
    with pytest.raises(SystemExit):
        guard.require_delivery_safe_update(strict=True)


def test_ordinary_delivery_guard_still_allows_identified_legacy(monkeypatch):
    from clover_cli import update_delivery_guard as guard
    monkeypatch.setattr(guard, '_unsafe_gateways', lambda: ['legacy: fixture: identified legacy runtime'])
    assert guard.require_delivery_safe_update()['status'] == 'legacy-proceed'


def test_staged_receipt_uses_target_checkout_not_controller(monkeypatch, tmp_path):
    from clover_cli import _migration_target, build_info, update_receipt
    target = tmp_path / 'target'
    (target / '.git').mkdir(parents=True)
    old = 'a' * 40
    new = 'b' * 40
    (target / '.git' / 'HEAD').write_text(old)
    monkeypatch.setattr(_migration_target, '_target', target)
    controller = build_info.get_code_identity()
    receipt = update_receipt.UpdateReceipt()
    assert receipt.data['pre_update']['sha'] == old
    (target / '.git' / 'HEAD').write_text(new)
    receipt.finalize('failed')
    assert receipt.data['post_update']['sha'] == new
    assert build_info.get_code_identity() == controller


def test_bound_cmd_update_runs_preflight_despite_reexec(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from clover_cli import main, config, update_contract, update_lock, update_preflight, _migration_target
    monkeypatch.setenv('CLOVER_UPDATE_REEXEC', '1')
    monkeypatch.setattr(main.os, '_exit', lambda code: None)
    monkeypatch.setattr(config, 'is_managed', lambda: False)
    monkeypatch.setattr(update_contract, 'evaluate_update_admission', lambda root: None)
    monkeypatch.setattr(_migration_target, '_target', tmp_path)
    monkeypatch.setattr(_migration_target, 'revalidate_target', lambda: None)
    monkeypatch.setattr(main, '_install_hangup_protection', lambda **kwargs: None)
    monkeypatch.setattr(main, '_finalize_update_output', lambda state: None)
    class Lock:
        def acquire(self): return True
        def release(self): pass
    monkeypatch.setattr(update_lock, 'UpdateLock', Lock)
    seen = []
    def gate(*args, **kwargs):
        seen.append('gate')
        return SimpleNamespace(blocked=True)
    monkeypatch.setattr(update_preflight, 'gate_cli_update', gate)
    monkeypatch.setattr(main, '_cmd_update_impl', lambda *a, **kw: seen.append('body'))
    with pytest.raises(SystemExit) as exc:
        main.cmd_update(SimpleNamespace())
    assert exc.value.code == update_preflight.PREFLIGHT_EXIT_BLOCKED
    assert seen == ['gate']


def test_fresh_entry_strips_internal_update_markers(tmp_path):
    import os, subprocess, sys
    target = tmp_path / 'target'
    home = tmp_path / 'home'
    target.mkdir()
    home.mkdir()
    script = str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'scripts' / 'staged_update.py')
    code = "import runpy,os,sys; m=runpy.run_path(sys.argv[1]); m['main'](sys.argv[2:]); print('MARKER='+str(os.environ.get('CLOVER_UPDATE_REEXEC')))"
    env = dict(os.environ, CLOVER_UPDATE_REEXEC='1')
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code, script, '--target', str(target), '--home', str(home), '--old-commit', 'a'*40, '--stage-commit', 'b'*40, '--stage-tree', 'c'*40], env=env, text=True, capture_output=True)
    assert 'MARKER=None' in result.stdout


def test_wrong_fetched_ref_leaves_source_and_local_config_unchanged(tmp_path):
    import subprocess
    from clover_cli import _migration_target as migration
    def git(root, *args):
        return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-b', 'main')
    git(source, 'config', 'user.name', 'Fixture')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    (source / 'clover_cli').mkdir()
    (source / 'clover_cli' / '__init__.py').write_text('')
    (source / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1"\n')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'old')
    old = git(source, 'rev-parse', 'HEAD')
    target = tmp_path / 'target'
    subprocess.run(['git', 'clone', str(source), str(target)], check=True, capture_output=True)
    git(target, 'config', 'core.autocrlf', 'true')
    (source / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="2"\n')
    git(source, 'commit', '-am', 'new')
    new = git(source, 'rev-parse', 'HEAD')
    stage = tmp_path / 'stage'
    subprocess.run(['git', 'clone', str(source), str(stage)], check=True, capture_output=True)
    git(source, 'reset', '--hard', old)
    before = (target / 'pyproject.toml').read_bytes(), (target / '.git' / 'config').read_bytes()
    with migration.bind_target(stage, target, new, old):
        with pytest.raises(ValueError, match='fetched target'):
            migration.prepare_staged_fetch('main')
    assert before == ((target / 'pyproject.toml').read_bytes(), (target / '.git' / 'config').read_bytes())


def test_config_migration_waits_for_staged_fetch(monkeypatch, tmp_path):
    from clover_cli import _migration_target, config
    home = tmp_path / 'home'
    home.mkdir()
    path = home / 'config.yaml'
    original = b'display:\n  skin: default\n'
    path.write_bytes(original)
    monkeypatch.setenv('CLOVER_HOME', str(home))
    monkeypatch.setattr(_migration_target, '_target', tmp_path)
    monkeypatch.setattr(_migration_target, '_identities', ('stage', 'target', 'new', 'old'))
    monkeypatch.setattr(_migration_target, '_prepared_commit', None)
    config.load_config()
    assert path.read_bytes() == original


def test_fleet_plan_uses_bound_installation_identity(monkeypatch, tmp_path):
    from clover_cli import _migration_target, update_inventory
    target = tmp_path / 'target'
    (target / '.git').mkdir(parents=True)
    (target / '.git' / 'HEAD').write_text('a' * 40)
    monkeypatch.setattr(_migration_target, '_target', target)
    plan = update_inventory.collect_runtime_inventory()
    assert plan.expected_sha == 'a' * 40
