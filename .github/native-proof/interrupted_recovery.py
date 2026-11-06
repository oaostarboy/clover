"""Observe recovery without starting or signalling any replacement process."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

import psutil
from interrupted_identity import birth


def check_sample(sample, *, old_pid, old_birth, accepted_heads):
    if (sample['pid'] == old_pid or sample['birth'] == old_birth
            or sample['listeners'] != [sample['pid']]
            or sample['head'] not in accepted_heads or sample['head'] != sample['code_sha']
            or sample['health'] != 'ok'):
        raise ValueError('recovery identity, source, listener or health mismatch')


def observe(home, target, port):
    state = json.loads((home / 'gateway_state.json').read_text())
    pid = state['pid']
    proc = psutil.Process(pid)
    ticks = birth(pid)
    if (proc.status() == psutil.STATUS_ZOMBIE or str(ticks) != str(state['start_time'])
            or state.get('gateway_state') != 'running' or state.get('restart_requested') is not False
            or proc.environ().get('CLOVER_HOME') != str(home)
            or proc.exe() != str((target / 'venv/bin/python').resolve())
            or proc.cwd() != str(target) or proc.uids().real != os.getuid()):
        raise ValueError('unowned or non-running recovery gateway')
    listeners = sorted({c.pid for c in psutil.net_connections(kind='inet')
                        if c.status == psutil.CONN_LISTEN and c.laddr.port == port})
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3) as response:
        health = json.load(response)['status'] if response.status == 200 else 'bad'
    head = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True, timeout=10).strip()
    return {'pid':pid, 'birth':ticks, 'listeners':listeners, 'head':head,
            'code_sha':state.get('code_sha'), 'health':health, 'observed_at_ns':time.time_ns(),
            'argv':proc.cmdline(), 'exe':proc.exe(), 'cwd':proc.cwd()}


def main():
    home, target, evidence = map(Path, sys.argv[1:4])
    old, new = sys.argv[4:6]
    fault = json.loads((evidence / 'interrupted-fault.json').read_text())
    if not fault.get('injected'):
        raise ValueError('fault was not injected')
    before = json.loads((evidence / 'before-state.json').read_text())
    deadline = time.monotonic() + 180
    while True:
        try:
            first = observe(home, target, 18791)
            check_sample(first, old_pid=before['pid'], old_birth=int(before['start_time']), accepted_heads={old,new})
            break
        except Exception:
            if time.monotonic() >= deadline: raise
            time.sleep(.5)
    start = time.monotonic(); samples = [dict(first, elapsed_seconds=0)]
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= 90: break
        time.sleep(min(1, 90-elapsed))
        current = observe(home, target, 18791)
        check_sample(current, old_pid=before['pid'], old_birth=int(before['start_time']), accepted_heads={old,new})
        if any(current[k] != first[k] for k in ('pid','birth','head','code_sha')):
            raise ValueError('gateway changed during stability interval')
        samples.append(dict(current, elapsed_seconds=time.monotonic()-start))
    if samples[-1]['elapsed_seconds'] < 90:
        raise ValueError('stability interval shorter than 90 seconds')
    stages = {e['stage']:e for e in fault['events']}
    if not (stages['watchers_stopped']['wall_ns'] < stages['updater_killed']['wall_ns']
            < stages['watchers_resumed']['wall_ns'] < first['observed_at_ns']):
        raise ValueError('fault/recovery ordering invalid')
    (evidence / 'interrupted-after-state.json').write_text((home / 'gateway_state.json').read_text())
    result = {'samples': samples, 'actual_head':first['head'], 'gateway_pid':first['pid'],
              'scope':'controlled crash, not a natural-race test'}
    (evidence / 'interrupted-recovery.json').write_text(json.dumps(result, indent=2)+'\n')
    for name in ('.clover-update-heartbeat.json', 'gateway_state.json'):
        path = home / name
        if path.is_file(): shutil.copy2(path, evidence / ('preserved-' + name.lstrip('.')))
    receipt = home / 'logs' / 'update_receipts'
    if receipt.is_dir(): shutil.copytree(receipt, evidence / 'preserved-update-receipts', dirs_exist_ok=True)
    print(first['head'])

if __name__ == '__main__': main()
