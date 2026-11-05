"""Real-process witness: a web build must exclude workspace installs."""
import os
from pathlib import Path
import subprocess
import sys
import time


def _await(path, process):
    deadline = time.monotonic() + 25
    while not path.exists() and time.monotonic() < deadline:
        assert process.poll() is None, f'worker exited early: {process.returncode}'
        time.sleep(.02)
    assert path.exists(), f'missing barrier: {path}'


def test_web_build_excludes_npm_writer(tmp_path):
    root = tmp_path / 'repo'
    web = root / 'web'
    web.mkdir(parents=True)
    (root / 'package.json').write_text('{"workspaces":["web"]}')
    (root / 'package-lock.json').write_text('{}')
    (web / 'package.json').write_text('{}')
    witness = tmp_path / 'witness'
    witness.mkdir()
    npm = tmp_path / 'npm'
    npm.write_text('''#!/usr/bin/env python3
import os, pathlib, sys, time
w = pathlib.Path(os.environ['WITNESS'])
if sys.argv[1:3] == ['run', 'build']:
    (w / 'building').touch()
    while not (w / 'release').exists(): time.sleep(.01)
elif os.environ.get('WRITER'):
    (w / 'writer-entered').touch()
''')
    npm.chmod(0o755)
    build = '''from pathlib import Path
import os
import clover_cli.main as m
m._web_ui_build_needed = lambda p: True
m._resolve_node_runtime_npm = lambda: os.environ['FAKE_NPM']
m._write_web_ui_build_stamp = lambda *a: None
assert m._build_web_ui(Path(os.environ['WEB']), fatal=True)
'''
    writer = '''from pathlib import Path
import os
from clover_cli.node_workspace import npm_workspace_lock
from clover_cli.main import _run_npm_install_deterministic
w=Path(os.environ['WITNESS'])
w.joinpath('writer-ready').touch()
try:
    with npm_workspace_lock(Path(os.environ['ROOT']), timeout=0.5):
        w.joinpath('writer-lock-acquired-early').touch()
except TimeoutError:
    w.joinpath('writer-blocked').touch()
assert _run_npm_install_deterministic(os.environ['FAKE_NPM'], Path(os.environ['ROOT'])).returncode == 0
'''
    env = dict(os.environ, WITNESS=str(witness), WEB=str(web), ROOT=str(root), FAKE_NPM=str(npm))
    processes = []
    try:
        b = subprocess.Popen([sys.executable, '-c', build], env=env)
        processes.append(b)
        _await(witness / 'building', b)
        w = subprocess.Popen([sys.executable, '-c', writer], env=dict(env, WRITER='1'))
        processes.append(w)
        _await(witness / 'writer-ready', w)
        deadline = time.monotonic() + 10
        while not any((witness / n).exists() for n in ('writer-blocked', 'writer-lock-acquired-early')) and time.monotonic() < deadline:
            assert w.poll() is None, f'writer exited: {w.returncode}'
            time.sleep(.02)
        assert (witness / 'writer-blocked').exists(), 'writer acquired lock during actual web build'
        assert not (witness / 'writer-entered').exists()
    finally:
        (witness / 'release').touch()
        for process in processes:
            try:
                process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    assert [p.returncode for p in processes] == [0, 0]
    assert (witness / 'writer-entered').exists()
