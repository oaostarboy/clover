"""Explicit migration binding must route installation queries to the target."""
import importlib
from pathlib import Path
import subprocess
import sys


def test_bound_installation_roots_and_purge(monkeypatch, tmp_path):
    from clover_cli import _migration_target as binding, config, update_cmd
    target = tmp_path.resolve()
    monkeypatch.setattr(binding, '_target', target)
    assert config.get_project_root() == target
    assert config._install_method_project_root() == target
    assert config._install_method_project_root(Path('/explicit')) == Path('/explicit')
    update_cmd._purge_stale_clover_modules()
    assert importlib.import_module('clover_cli._migration_target') is binding
    assert importlib.import_module('clover_cli.config').get_project_root() == target


def test_main_import_after_binding_uses_target(tmp_path):
    stage = Path(__file__).resolve().parents[2]
    code = """import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from clover_cli import _migration_target as binding
binding._target = Path(sys.argv[2])
from clover_cli import main
assert main.PROJECT_ROOT == binding._target, (main.PROJECT_ROOT, binding._target)
"""
    result = subprocess.run([sys.executable, '-I', '-c', code, str(stage), str(tmp_path)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
