#!/usr/bin/env python3
"""Bounded read-only snapshots of a disposable hosted Mac's fixture port."""
import datetime, json, os, subprocess, sys, time
from pathlib import Path

def capture(port=18791, runner=subprocess.run):
    commands = [["lsof", "-nP", f"-iTCP:{port}"], ["netstat", "-anv", "-p", "tcp"], ["ps", "-axo", "pid,ppid,lstart,command"], ["launchctl", "print", f"gui/{os.getuid()}/ai.clover.gateway"]]
    result = {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "monotonic_ns": time.monotonic_ns(), "commands": []}
    for cmd in commands:
        try:
            p = runner(cmd, capture_output=True, text=True, timeout=4, check=False)
            out = p.stdout
            if cmd[0] == 'netstat':
                out = '\n'.join(line for line in out.splitlines() if f'.{port}' in line or f':{port}' in line)
            elif cmd[0] == 'ps':
                # Filter before applying the output cap: system processes on
                # hosted macOS can otherwise hide all fixture process IDs.
                lines = out.splitlines()
                terms = ('native-mac-upgrade-', 'npm', 'node', 'electron', 'clover')
                out = '\n'.join(lines[:1] + [line for line in lines[1:]
                                              if any(term in line.lower() for term in terms)])
            result['commands'].append({'argv': cmd, 'rc': p.returncode, 'out': out[:30000], 'err': p.stderr[:1000]})
        except (OSError, subprocess.TimeoutExpired) as e:
            result['commands'].append({'argv': cmd, 'error': str(e)})
    return result

def main():
    if sys.platform != 'darwin': raise SystemExit('disposable macOS only')
    target, stop = map(Path, sys.argv[1:3])
    end = time.monotonic() + 1200
    with target.open('w') as out:
        while time.monotonic() < end and not stop.exists():
            started = time.monotonic()
            out.write(json.dumps(capture())+'\n'); out.flush()
            time.sleep(max(0, 1-(time.monotonic()-started)))
        out.write(json.dumps({'end_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'stopped': stop.exists()})+'\n')
if __name__ == '__main__': main()
