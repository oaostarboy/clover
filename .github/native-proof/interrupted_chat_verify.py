"""Check fresh IRC nonce, shared session, config, and final live identity."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from interrupted_recovery import observe

home, target, evidence = map(Path, sys.argv[1:])
nonce = 'RECOVERY_NONCE_' + json.loads((evidence / 'recovery-nonce.json').read_text())['nonce']
wire = json.loads((evidence / 'chat-post-reply.json').read_text())
if not any(nonce in msg for msg in wire['messages']):
    raise ValueError('fresh nonce was not returned over IRC')
pre_hash = (evidence / 'config-before.sha256').read_text().strip()
post_hash = hashlib.sha256((home / 'config.yaml').read_bytes()).hexdigest()
if pre_hash != post_hash:
    raise ValueError('configuration changed')

def session_check():
    with sqlite3.connect('file:' + str(home / 'state.db') + '?mode=ro', uri=True) as db:
        rows = db.execute('select session_id, role, content from messages order by rowid').fetchall()
    pre = {sid for sid, role, content in rows if role == 'user' and 'STAGE_HISTORY_SENTINEL' in str(content)}
    for i, (sid, role, content) in enumerate(rows):
        if role == 'user' and nonce in str(content) and sid in pre:
            for sid2, role2, content2 in rows[i+1:]:
                if sid2 != sid: continue
                if role2 == 'user': break
                if role2 == 'assistant' and nonce in str(content2): return sid
    return None

end = time.monotonic() + 120
while True:
    try:
        session = session_check()
        if session: break
    except sqlite3.Error:
        pass
    if time.monotonic() >= end: raise ValueError('nonce turn and history were not in the same session')
    time.sleep(.25)
recovery = json.loads((evidence / 'interrupted-recovery.json').read_text())
final = observe(home, target, 18791)
first = recovery['samples'][0]
if any(final[k] != first[k] for k in ('pid','birth','head','code_sha','listeners')):
    raise ValueError('gateway changed after recovery window')
observed = []
for path in evidence.glob('live-modules-*.json'):
    try:
        data = json.loads(path.read_text())
        pid = data['pid']
        modules = data['modules']
        if (type(pid) is not int or pid <= 1 or path.name != f'live-modules-{pid}.json'
                or not isinstance(modules, dict) or 'gateway.run' not in modules
                or any(not isinstance(k, str) or (v is not None and not isinstance(v, str))
                       for k, v in modules.items())):
            raise ValueError('invalid live module observation')
        if modules['gateway.run']:
            observed.append(pid)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('invalid live module observation')
(evidence / 'interrupted-chat.json').write_text(json.dumps({
    'nonce': nonce, 'history_sentinel': 'STAGE_HISTORY_SENTINEL',
    'session_id': session, 'config_sha256': post_hash,
    'final_sample': final, 'module_import_pids': sorted(set(observed)),
    'launch_accounting': 'pending',
    'launch_observation_limit': 'module imports do not establish launch counts; kernel-trace accounting is required'
}, indent=2)+'\n')
