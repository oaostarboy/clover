"""A real desktop build must exclude concurrent workspace installs."""
import os
import subprocess
import sys
import time


def wait_for(path, process):
    deadline = time.monotonic() + 25
    while not path.exists() and time.monotonic() < deadline:
        assert process.poll() is None, f'worker exited: {process.returncode}'
        time.sleep(.02)
    assert path.exists(), f'missing barrier: {path}'


def test_desktop_build_excludes_npm_writer(tmp_path):
    root = tmp_path / 'repo'
    desktop = root / 'apps' / 'desktop'
    desktop.mkdir(parents=True)
    (root / 'package.json').write_text('{"workspaces":["apps/*"]}')
    (root / 'package-lock.json').write_text('{}')
    (desktop / 'package.json').write_text('{}')
    witness = tmp_path / 'witness'
    witness.mkdir()
    npm = tmp_path / 'npm'
    npm.write_text('''#!/usr/bin/env python3
import os, pathlib, sys, time
w = pathlib.Path(os.environ['WITNESS'])
if sys.argv[1:3] == ['run', 'build']:
    (w / 'building').touch()
    while not (w / 'release').exists(): time.sleep(.01)
    (pathlib.Path(os.environ['DESKTOP']) / 'dist').mkdir(exist_ok=True)
elif os.environ.get('WRITER'):
    (w / 'writer-entered').touch()
''')
    npm.chmod(0o755)
    build = '''from pathlib import Path
from argparse import Namespace
import os
import clover_cli.main as m
m.PROJECT_ROOT=Path(os.environ['ROOT'])
m._resolve_node_runtime_npm=lambda: os.environ['FAKE_NPM']
m._desktop_launch_options=lambda: ([], 'auto', 'auto', 'auto')
m._desktop_packaged_executable=lambda *a: None
m._desktop_dist_exists=lambda p: (p / 'dist').exists()
m._write_desktop_build_stamp=lambda *a, **k: None
m._register_desktop_launchers=lambda *a, **k: None
m.cmd_gui(Namespace(source=True, force_build=True, build_only=True))
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
    env = dict(os.environ, WITNESS=str(witness), ROOT=str(root), DESKTOP=str(desktop), FAKE_NPM=str(npm))
    processes = []
    try:
        b = subprocess.Popen([sys.executable, '-c', build], env=env)
        processes.append(b)
        wait_for(witness / 'building', b)
        w = subprocess.Popen([sys.executable, '-c', writer], env=dict(env, WRITER='1'))
        processes.append(w)
        wait_for(witness / 'writer-ready', w)
        deadline = time.monotonic() + 10
        while not any((witness / n).exists() for n in ('writer-blocked', 'writer-lock-acquired-early')) and time.monotonic() < deadline:
            assert w.poll() is None, f'writer exited: {w.returncode}'
            time.sleep(.02)
        assert (witness / 'writer-blocked').exists(), 'writer acquired lock during actual desktop build'
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
