"""Fixture process roots only; hosted macOS, no signals or raw argv/env output."""
import json
import os
from pathlib import Path
import sys
import time

if sys.platform != 'darwin' or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise SystemExit(2)
import psutil

runner, installer, target, shell_pid, parent_pid = sys.argv[1:]
root = Path(runner)
output = root / 'evidence/macos-known-roots.json'
stop = root / 'evidence/macos-roots-stop'
known = {}

def add(proc, role):
    try:
        birth = proc.create_time()
        key = (proc.pid, birth, role)
        known[key] = {'pid': proc.pid, 'birth': birth, 'ppid': proc.ppid(), 'role': role}
    except (psutil.Error, OSError):
        pass

for role, pid in [('runner_shell', int(shell_pid)), ('runner_parent', int(parent_pid))]:
    try:
        add(psutil.Process(pid), role)
    except (psutil.Error, OSError):
        pass

deadline = time.monotonic() + 2700
while time.monotonic() < deadline and not stop.exists():
    for proc in psutil.process_iter():
        try:
            argv = proc.cmdline()
            if installer in argv:
                add(proc, 'installer')
            if 'update' in argv and any(arg.startswith(target + '/') for arg in argv):
                add(proc, 'updater')
        except (psutil.Error, OSError):
            pass
    tmp = output.with_suffix('.tmp')
    tmp.write_text(json.dumps({'kind': 'observed process identities, not ownership authorization',
                               'roots': list(known.values())}, indent=2))
    tmp.replace(output)
    time.sleep(.5)
