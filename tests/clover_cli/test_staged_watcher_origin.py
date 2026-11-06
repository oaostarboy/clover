"""The staged controller must not leak its import root into its child."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

from clover_cli import gateway, _migration_target, update_cmd, update_restart_watcher


@pytest.mark.skipif(os.name == 'nt', reason='POSIX detached-watcher reproduction')
def test_bound_watcher_runs_target_module_not_stage(tmp_path, monkeypatch):
    stage, target = tmp_path / 'stage', tmp_path / 'target'
    proof = tmp_path / 'origin'
    for root in (stage, target):
        (root / 'clover_cli').mkdir(parents=True)
        (root / 'gateway').mkdir()
        (root / 'clover_cli/__init__.py').write_text('')
        (root / 'gateway/__init__.py').write_text('')
        (root / 'clover_cli/_subprocess_compat.py').write_text(
            'def windows_detach_flags(): return 0\n'
            'def windows_detach_flags_without_breakaway(): return 0\n')
        (root / 'gateway/status.py').write_text('def _pid_exists(pid): return False\n')
        (root / 'clover_cli/main.py').write_text(
            f'from pathlib import Path\nPath({str(proof)!r}).write_text(__file__)\n')
    monkeypatch.chdir(stage)
    monkeypatch.setenv('PYTHONPATH', str(stage))
    monkeypatch.setattr(_migration_target, 'target_root', lambda: target)
    monkeypatch.setattr(_migration_target, 'target_python', lambda: Path(sys.executable))
    monkeypatch.setattr(_migration_target, 'validate_recovery_source', lambda: None)
    assert gateway._spawn_gateway_restart_watcher(999999, [sys.executable, '-m', 'clover_cli.main'])
    for _ in range(100):
        if proof.exists():
            break
        time.sleep(.05)
    assert proof.exists(), 'watcher did not start the child'
    assert Path(proof.read_text()).is_relative_to(target)


@pytest.mark.parametrize('failure', ['unprepared', 'missing-runtime'])
def test_bound_watcher_refuses_invalid_target(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(_migration_target, 'target_root', lambda: tmp_path)
    def invalid():
        raise ValueError(failure)
    monkeypatch.setattr(_migration_target, 'validate_recovery_source',
                        invalid if failure == 'unprepared' else lambda: None)
    monkeypatch.setattr(_migration_target, 'target_python',
                        invalid if failure == 'missing-runtime' else lambda: Path(sys.executable))
    monkeypatch.setattr(gateway.subprocess, 'Popen', lambda *a, **k: pytest.fail('spawned'))
    assert not gateway._spawn_gateway_restart_watcher(1234, [sys.executable, '-m', 'clover_cli.main'])


def test_bound_watcher_refuses_unvalidated_script(monkeypatch, tmp_path):
    monkeypatch.setattr(_migration_target, 'target_root', lambda: tmp_path)
    monkeypatch.setattr(_migration_target, 'target_python', lambda: Path(sys.executable))
    monkeypatch.setattr(_migration_target, 'validate_recovery_source', lambda: None)
    monkeypatch.setattr(gateway.subprocess, 'Popen', lambda *a, **k: pytest.fail('spawned'))
    assert not gateway._spawn_gateway_restart_watcher(1234, [sys.executable, '/tmp/stage/main.py'])


def test_staged_context_removes_python_overrides(monkeypatch, tmp_path):
    monkeypatch.setattr(_migration_target, 'target_root', lambda: tmp_path)
    monkeypatch.setattr(_migration_target, 'target_python', lambda: tmp_path / 'venv/bin/python')
    monkeypatch.setattr(_migration_target, 'validate_recovery_source', lambda: None)
    monkeypatch.setenv('PYTHONPATH', '/stage')
    monkeypatch.setenv('PYTHONHOME', '/stage')
    monkeypatch.setenv('PYTHONUSERBASE', '/stage')
    root, env, python = gateway._staged_gateway_child_context()
    assert root == str(tmp_path)
    assert python == str(tmp_path / 'venv/bin/python')
    assert env['VIRTUAL_ENV'] == str(tmp_path / 'venv')
    assert not any(key.startswith('PYTHON') for key in env)


def test_unbound_context_does_not_change_existing_behavior(monkeypatch):
    monkeypatch.setattr(_migration_target, 'target_root', lambda: None)
    assert gateway._staged_gateway_child_context() is None


def test_pre_pull_beacon_and_frozen_watcher_use_target(monkeypatch, tmp_path):
    stage = tmp_path / 'stage'
    stage.mkdir()
    target = tmp_path / 'target'
    target.mkdir()
    python = target / 'venv/bin/python'
    monkeypatch.chdir(stage)
    monkeypatch.setenv('PYTHONPATH', str(stage))
    monkeypatch.setattr(_migration_target, 'target_root', lambda: target)
    monkeypatch.setattr(_migration_target, 'target_python', lambda: python)
    monkeypatch.setattr(_migration_target, 'revalidate_target', lambda: None)
    path = update_restart_watcher.write_beacon(['gateway'], clover_home=tmp_path / 'home')
    beacon = json.loads(path.read_text())
    assert beacon['cwd'] == str(target)
    assert beacon['venv_python'] == str(python)
    context = update_cmd._staged_frozen_watcher_context()
    assert context['cwd'] == str(target)
    assert 'PYTHONPATH' not in context['env']
    assert context['env']['VIRTUAL_ENV'] == str(target / 'venv')
