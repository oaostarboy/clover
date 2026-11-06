"""Read-only hosted macOS process topology. No argv/env output or signals."""
import argparse
import json
import os
from pathlib import Path
import sys
import time


def permitted(platform, env):
    return (platform == 'darwin' and env.get('GITHUB_ACTIONS') == 'true'
            and env.get('RUNNER_ENVIRONMENT') == 'github-hosted')


def within(value, root):
    try:
        return Path(value).is_relative_to(root)
    except (TypeError, ValueError):
        return False


def ancestry(pid, rows):
    chain, seen = [], set()
    while isinstance(pid, int) and pid in rows and pid not in seen and len(chain) < 64:
        seen.add(pid)
        row = rows[pid]
        chain.append({'pid': pid, 'birth': row.get('birth')})
        pid = row.get('ppid')
    return {'chain': chain, 'boundary_pid': pid, 'cycle': isinstance(pid, int) and pid in seen}


def observe(psutil, roots):
    rows = {}
    errors = []
    # Enumerate current-user processes and readable parent identity without
    # collecting command contents or environment. Denied access stays explicit.
    for proc in psutil.process_iter():
        try:
            uids = list(proc.uids())
            if os.getuid() not in uids:
                continue
            row = {'pid': proc.pid, 'uids': uids}
            for name, method in [('birth', proc.create_time), ('ppid', proc.ppid)]:
                try:
                    row[name] = method()
                except (psutil.Error, OSError) as exc:
                    row[name] = {'error': type(exc).__name__}
            for name, method in [('exe', proc.exe), ('cwd', proc.cwd)]:
                try:
                    value = method()
                    row[name] = {'basename': Path(value).name if name == 'exe' else None,
                                 'root_membership': {k: within(value, v) for k, v in roots.items()}}
                except (psutil.Error, OSError) as exc:
                    row[name] = {'error': type(exc).__name__}
            try:
                # Only equality/path membership booleans leave this process.
                argv = proc.cmdline()
                row['cmdline'] = {'readable': True,
                    'root_argument': {k: any(within(a, v) for a in argv if os.path.isabs(a))
                                      for k, v in roots.items()}}
            except (psutil.Error, OSError) as exc:
                row['cmdline'] = {'readable': False, 'error': type(exc).__name__}
            rows[proc.pid] = row
        except (psutil.Error, OSError) as exc:
            errors.append({'pid': proc.pid, 'error': type(exc).__name__})
    # Process identities are observations, never authorization to control them.
    for row in rows.values():
        ppid = row.get('ppid')
        row['ancestry'] = ancestry(row['pid'], rows) if isinstance(ppid, int) else {'error': 'parent unavailable'}
    return {'monotonic': time.monotonic(), 'processes': list(rows.values()), 'errors': errors}


def main():
    if not permitted(sys.platform, os.environ):
        print(json.dumps({'status': 'refused', 'reason': 'requires disposable GitHub-hosted macOS'}))
        return 2
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--venv', type=Path, required=True)
    parser.add_argument('--profile-home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = Path(os.environ['RUNNER_TEMP']).resolve()
    if not args.output.resolve().is_relative_to(base):
        parser.error('output must be inside disposable RUNNER_TEMP')
    import psutil
    roots = {'target': args.target.absolute(), 'venv': args.venv.absolute(),
             'profile': args.profile_home.absolute()}
    samples = [observe(psutil, roots)]
    time.sleep(1)
    samples.append(observe(psutil, roots))
    payload = {'kind': 'read-only diagnostic, not ownership authorization or acceptance',
               'run_id': os.environ.get('GITHUB_RUN_ID'), 'samples': samples}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
