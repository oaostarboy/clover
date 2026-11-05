"""Run the real purge in a child interpreter, not inside pytest's imports."""
from pathlib import Path
import subprocess
import sys


def test_update_transaction_keeps_one_owner_through_real_purge():
    code = '''
import importlib
from clover_cli import update_cmd, macos_update_transaction as original
from clover_cli import macos_update_lifecycle as lifecycle
from clover_cli import macos_update_launchd as launchd
@original.source_update_scope
def update():
    owner = original._current.get()
    update_cmd._reload_updated_runtime_modules()
    update_cmd._purge_stale_clover_modules()
    fresh = importlib.import_module('clover_cli.macos_update_transaction')
    assert fresh is original, 'purge created a second transaction module'
    assert importlib.import_module('clover_cli.macos_update_lifecycle') is lifecycle
    assert importlib.import_module('clover_cli.macos_update_launchd') is launchd
    @fresh.source_update_scope
    def nested():
        assert fresh._current.get() is owner, 'nested scope lost its owner'
    nested()
update()
assert original._current.get() is None
print('single-owner-through-purge')
'''
    result = subprocess.run([sys.executable, '-c', code],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'single-owner-through-purge' in result.stdout
