"""Strict host evidence checks for the private Linux staged-updater cell."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

mode, home, target, stage, evidence, state = sys.argv[1:]
home, target, stage, evidence, state = map(Path, (home, target, stage, evidence, state))

def wait_for(fn, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = fn()
            if result:
                return result
        except (OSError, ValueError, sqlite3.Error):
            pass
        time.sleep(.25)
    raise RuntimeError('timed out waiting for ' + mode)

if mode == 'identity':
    pid = json.loads(state.read_text())['pid']
    proc = Path('/proc') / str(pid)
    cmdline = (proc / 'cmdline').read_bytes().split(b'\0')
    argv = [x.decode() for x in cmdline if x]
    cwd = os.readlink(proc / 'cwd')
    exe = os.readlink(proc / 'exe')
    observer = wait_for(lambda: json.loads((evidence / f'live-modules-{pid}.json').read_text()))
    assert observer['pid'] == pid
    assert Path(cwd).resolve() == target.resolve(), (cwd, target)
    assert Path(exe).resolve() == (target / 'venv/bin/python').resolve(), exe
    assert argv and Path(argv[0]) == target / 'venv/bin/python', argv
    assert not any(str(stage) in arg for arg in argv), argv
    for name in ('__main__', 'gateway.run'):
        origin = Path(observer['modules'][name]).resolve()
        assert origin.is_relative_to(target.resolve()), (name, origin)
    output = {'pid': pid, 'exe': exe, 'argv': argv, 'cwd': cwd,
              'live_process_modules_fixture': observer['modules']}
    (evidence / (state.stem + '-os-identity.json')).write_text(json.dumps(output, indent=2))
elif mode == 'session':
    def snapshot():
        with sqlite3.connect('file:' + str(home / 'state.db') + '?mode=ro', uri=True) as db:
            tables = [r[0] for r in db.execute("select name from sqlite_master where type='table' and name in ('sessions','messages')")]
            return {name: {'columns': [r[1] for r in db.execute('pragma table_info(' + name + ')')],
                           'rows': [list(r) for r in db.execute('select * from ' + name)]} for name in tables}
    def complete():
        data = snapshot()
        text = json.dumps(data, default=str)
        return data if all(s in text for s in ('STAGE_HISTORY_SENTINEL', 'STAGE_INDEPENDENCE_TURN', 'Local acceptance auxiliary response.')) else None
    data = wait_for(complete, 120)
    config = home / 'config.yaml'
    before = (evidence / 'config-before.sha256').read_text().strip()
    after = hashlib.sha256(config.read_bytes()).hexdigest()
    (evidence / 'stage-session.json').write_text(json.dumps({'state': data, 'config_sha256_before': before, 'config_sha256_after': after}, default=str, indent=2))
    (evidence / 'config-after-chat.yaml').write_bytes(config.read_bytes())
    assert before == after, (before, after)
else:
    raise ValueError(mode)
