"""Read-only process inspection; never emit arguments or environment."""
import json
import os
from pathlib import Path
import subprocess
import sys
import psutil

if sys.platform != 'darwin' or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise SystemExit('disposable hosted Darwin required')
out = Path(sys.argv[1]).resolve()
if not out.is_relative_to(Path(os.environ['RUNNER_TEMP']).resolve()):
    raise SystemExit('output outside runner temp')
rows = []
for p in psutil.process_iter():
    try:
        uids = list(p.uids())
        if os.getuid() not in uids:
            continue
        birth = p.create_time()
        try:
            args = p.cmdline()
            status = 'readable'
        except (psutil.Error, OSError) as e:
            args = None
            status = type(e).__name__
        if args is not None:
            continue
        r = subprocess.run(['/bin/ps', '-ww', '-p', str(p.pid), '-o', 'command='], capture_output=True, text=True, timeout=5)
        text = r.stdout.strip()
        try:
            after = psutil.Process(p.pid)
            same = after.create_time() == birth and list(after.uids()) == uids
        except (psutil.Error, OSError):
            same = False
        rows.append({'pid': p.pid, 'birth': birth, 'uids': uids, 'psutil': status, 'ps_exit': r.returncode, 'ps_nonempty': bool(text), 'ps_placeholder': text in ('', '???', '?', '<defunct>'), 'identity_revalidated': same})
    except (psutil.Error, OSError, subprocess.SubprocessError):
        continue
out.write_text(json.dumps({'kind': 'read-only availability probe; ps formatting does not prove exact argv', 'rows': rows}, indent=2))
