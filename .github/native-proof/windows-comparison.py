"""Test-only Windows timing probe; no product or user state changes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

root = Path(os.environ['GITHUB_WORKSPACE'])
out = Path(os.environ['RUNNER_TEMP']) / 'windows-comparison'
out.mkdir(exist_ok=True)
env = dict(os.environ, CLOVER_SAFE_MODE='1', PYTHONFAULTHANDLER='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
base = [sys.executable, '-m', 'pytest', '-p', 'pytest_asyncio.plugin']
common = ['-vv', '-s', '--durations=0', '-o', 'faulthandler_timeout=30']
selector = [
 'not test_acceptance_fails_closed or (helper_prose or untrusted or zero_only or stale_report or failed_test)',
 'test_acceptance_fails_closed and (skipped_test or empty_report or bad_report or report_totals or changed_artifact or changed_spec or changed_evidence or changed_revision or wrong_cwd or wrong_session or new_session or foreign_id)',
 'test_acceptance_fails_closed and not ((helper_prose or untrusted or zero_only or stale_report or failed_test) or (skipped_test or empty_report or bad_report or report_totals or changed_artifact or changed_spec or changed_evidence or changed_revision or wrong_cwd or wrong_session or new_session or foreign_id))',
]

def launch(name, args, cwd):
 log = (out / (name + '.log')).open('wb')
 start = time.monotonic()
 proc = subprocess.Popen(base + args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
 return name, proc, log, start

def finish(item, timeout=320):
 name, proc, log, start = item
 try:
  rc = proc.wait(timeout=max(0.01, timeout - (time.monotonic() - start)))
 except subprocess.TimeoutExpired:
  subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  proc.wait(); rc=124
 log.close()
 return {'name': name, 'rc': rc, 'elapsed_seconds': round(time.monotonic()-start,2)}

# Baseline uses exactly the product's new test with the old source tree.
old = root / 'baseline'
shutil.copy(root / 'source/tests/agent/test_handoff_windows_permissions.py', old / 'tests/agent/test_windows_repair_probe.py')
results = [finish(launch('old-red', ['tests/agent/test_windows_repair_probe.py'] + common, old), 90)]
shutil.copy(root / 'helper/.github/native-proof/test_windows_handoff_native_probe.py', root / 'source/tests/agent/test_windows_handoff_native_probe.py')
results.append(finish(launch('native-security', ['tests/agent/test_windows_handoff_native_probe.py'] + common, root / 'source'), 90))
items = [launch(f'parallel-{i+1}', ['tests/gateway/test_workstream_acceptance.py', '-k', s] + common, root / 'source') for i,s in enumerate(selector)]
results.extend(finish(item, 320) for item in items)
(out/'results.json').write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
# RED must be a real failing test and every comparison batch must run successfully.
oldlog = (out/'old-red.log').read_text(errors='replace')
if results[0]['rc'] != 1 or 'fchmod' not in oldlog or any(x['rc'] != 0 for x in results[1:]):
 sys.exit(1)
