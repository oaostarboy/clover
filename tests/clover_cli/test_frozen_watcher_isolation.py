"""Actual isolated watcher startup; all process work is namespace-sandboxed."""
import json
import os
from pathlib import Path
import subprocess
import sys

import psutil
import pytest

from clover_cli import _migration_target as binding


def test_frozen_watcher_skips_target_hooks_and_keeps_psutil(tmp_path, monkeypatch):
    target = tmp_path / 'target'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(target)], check=True)
    python = target / 'bin/python'
    site = next((target / 'lib').glob('python*/site-packages'))
    marker = tmp_path / 'hook'
    (site / 'sitecustomize.py').write_text(f'from pathlib import Path; Path({str(marker)!r}).touch()')
    (site / 'evil.pth').write_text(f'import pathlib; pathlib.Path({str(marker)!r}).touch()\n')
    frozen = tmp_path / 'frozen.py'
    frozen.write_text('import psutil,json,sys; print(json.dumps({"birth":psutil.Process().create_time(),"args":sys.argv[1:]}))')
    monkeypatch.setattr(binding, '_target', target)
    monkeypatch.setattr(binding, 'trusted_dependency_paths', lambda: (Path(psutil.__file__).resolve().parent.parent,))
    monkeypatch.setattr(sys, 'executable', str(python))
    command = binding.frozen_watcher_command(frozen, tmp_path / 'beacon')
    assert command[1:3] == ['-I', '-S']
    result = subprocess.run(command, cwd=target, capture_output=True, text=True, check=True)
    assert not marker.exists()
    row = json.loads(result.stdout)
    assert row['birth'] > 0
    assert row['args'] == [str(tmp_path / 'beacon')]


def test_missing_psutil_refuses_before_watcher_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(binding, '_target', tmp_path)
    monkeypatch.setattr(binding, 'trusted_dependency_paths', lambda: (tmp_path,))
    with pytest.raises(ValueError, match='trusted watcher'):
        binding.frozen_watcher_command(tmp_path / 'frozen.py', tmp_path / 'beacon')


def test_dependency_binding_is_immutable_revalidated_and_cleared(tmp_path, monkeypatch):
    stage, target, deps = (tmp_path / n for n in ('stage', 'target', 'deps'))
    for path in (stage, target, deps):
        path.mkdir()
    monkeypatch.setattr(binding, '_physical_root', lambda p: p)
    monkeypatch.setattr(binding, '_verify_git', lambda *a: None)
    paths = [deps]
    with binding.bind_target(stage, target, 'a' * 40, 'b' * 40, dependency_paths=paths):
        paths.clear()
        assert binding.trusted_dependency_paths() == (deps,)
        deps.rename(tmp_path / 'old-deps')
        deps.mkdir()
        with pytest.raises(ValueError, match='identity changed'):
            binding.trusted_dependency_paths()
    assert binding.trusted_dependency_paths() == ()
    assert binding._trusted_dependencies == ()
    with pytest.raises(ValueError, match='disjoint'):
        with binding.bind_target(stage, target, 'a' * 40, 'b' * 40, dependency_paths=[target]):
            pytest.fail('overlapping dependency admitted')


def test_unbound_watcher_command_unchanged(tmp_path):
    assert binding.frozen_watcher_command(tmp_path / 'frozen.py', tmp_path / 'beacon') == [
        sys.executable, str(tmp_path / 'frozen.py'), str(tmp_path / 'beacon')]
