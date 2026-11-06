"""Compare controller validation with the pinned product command builder."""
import ast
import hashlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest
import interrupted_controller as controller
from interrupted_identity import Refusal

ROOT = Path(__file__).resolve().parents[2]


def product_command(runtime, frozen, beacon, roots):
    # Execute only the exact command-construction function, with its preflight
    # replaced by a successful no-side-effect result. No updater is imported.
    raw = subprocess.check_output(['git', 'show',
        '826e8ff178dadf5c5eb929ea3ef4c8bb27d30b6d:clover_cli/_migration_target.py'], cwd=ROOT).decode()
    node = next(n for n in ast.parse(raw).body if isinstance(n, ast.FunctionDef)
                and n.name == 'frozen_watcher_command')
    scope = {'_target': object(), 'trusted_dependency_paths': lambda: roots,
             'sys': SimpleNamespace(executable=runtime),
             'os': SimpleNamespace(environ={}),
             'subprocess': SimpleNamespace(DEVNULL=-3, TimeoutExpired=subprocess.TimeoutExpired,
                  run=lambda *a, **k: SimpleNamespace(returncode=0, stdout='trusted-watcher-ready'))}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<pinned-command-builder>', 'exec'), scope)
    return scope['frozen_watcher_command'](frozen, beacon)


def test_controller_accepts_exact_isolated_pinned_command():
    runtime='/fixture/runtime/bin/python'; frozen='/fixture/home/logs/update_restart_watcher.py'
    beacon='/fixture/home/.clover-update-heartbeat.json'; roots=['/fixture/runtime/lib/site-packages']
    argv=product_command(runtime,frozen,beacon,roots)
    controller.validate_frozen_command(argv,runtime,frozen,beacon,roots)
    for bad in ([runtime,frozen,beacon], argv[:4]+['print("untrusted")']+argv[5:],
                ['/different/python']+argv[1:], argv+['--extra']):
        with pytest.raises(Refusal):
            controller.validate_frozen_command(bad,runtime,frozen,beacon,roots)
