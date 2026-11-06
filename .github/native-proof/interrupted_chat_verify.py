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
        if data.get('modules', {}).get('gateway.run'): observed.append(data['pid'])
    except (OSError, ValueError, KeyError):
        raise ValueError('invalid live module observation')
before = json.loads((evidence / 'before-state.json').read_text())
if set(observed) - {before['pid'], first['pid']}:
    raise ValueError('additional gateway module startup observed')
(evidence / 'interrupted-chat.json').write_text(json.dumps({
    'nonce': nonce, 'session_id': session, 'config_sha256': post_hash,
    'final_sample': final, 'gateway_module_startup_pids': sorted(set(observed)),
    'launch_observation_limit': 'module-origin observer; does not capture processes that exit before importing gateway.run'
}, indent=2)+'\n')
