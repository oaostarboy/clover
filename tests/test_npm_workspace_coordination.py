"""Cross-process witness for npm workspace mutations."""
import os
from pathlib import Path
import subprocess
import sys
import time


def test_same_workspace_npm_writers_do_not_overlap(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'package-lock.json').write_text('{}')
    witness = tmp_path / 'witness'
    witness.mkdir()
    npm = tmp_path / 'npm'
    npm.write_text('''#!/usr/bin/env python3
import os, pathlib, time
w = pathlib.Path(os.environ['WITNESS'])
(w / os.environ['WORKER']).write_text('entered')
while not (w / 'release').exists(): time.sleep(.01)
''')
    npm.chmod(0o755)
    code = 'from pathlib import Path; from clover_cli.main import _run_npm_install_deterministic; import sys, os; Path(os.environ["WITNESS"], os.environ["WORKER"] + "-ready").touch(); sys.exit(_run_npm_install_deterministic(sys.argv[1], Path(sys.argv[2])).returncode)'
    processes = []
    try:
        for name in ('first', 'second'):
            env = dict(os.environ, WITNESS=str(witness), WORKER=name)
            processes.append(subprocess.Popen([sys.executable, '-c', code, str(npm), str(root)], env=env))
            if name == 'first':
                deadline = time.monotonic() + 20
                while not (witness / name).exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                assert (witness / name).exists(), 'first npm did not enter'
        deadline = time.monotonic() + 20
        while not (witness / 'second-ready').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (witness / 'second-ready').exists(), 'second helper did not start'
        deadline = time.monotonic() + 3
        while not (witness / 'second').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert not (witness / 'second').exists(), 'second npm entered while first npm held workspace'
    finally:
        (witness / 'release').touch()
        for p in processes:
            try: p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
    assert [p.returncode for p in processes] == [0, 0]
    assert (witness / 'second').exists(), 'second npm did not run after release'
