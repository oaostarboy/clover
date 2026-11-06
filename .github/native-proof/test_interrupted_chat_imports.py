import hashlib
import json
from pathlib import Path
import runpy
import sqlite3
import sys
from unittest.mock import patch

import pytest

PROOF = Path(__file__).parent
HOSTED = PROOF / 'fixtures/hosted-42267002884-module-imports.json'


def test_hosted_import_observations_do_not_claim_extra_launches(tmp_path):
    home = tmp_path / 'home'; home.mkdir()
    evidence = tmp_path / 'evidence'; evidence.mkdir()
    target = tmp_path / 'target'; target.mkdir()
    for record in json.loads(HOSTED.read_text()):
        (evidence / f"live-modules-{record['pid']}.json").write_text(json.dumps(record))
    nonce = 'RECOVERY_NONCE_fixture'
    (home / 'config.yaml').write_text('fixture')
    (evidence / 'config-before.sha256').write_text(hashlib.sha256(b'fixture').hexdigest())
    (evidence / 'recovery-nonce.json').write_text(json.dumps({'nonce':'fixture'}))
    (evidence / 'chat-post-reply.json').write_text(json.dumps({'messages':[nonce]}))
    final = {'pid':5608,'birth':10,'head':'sha','code_sha':'sha','listeners':[5608]}
    (evidence / 'interrupted-recovery.json').write_text(json.dumps({'samples':[final]}))
    (evidence / 'before-state.json').write_text(json.dumps({'pid':4490}))
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('create table messages(session_id, role, content)')
        db.executemany('insert into messages values (?, ?, ?)', [('s','user','STAGE_HISTORY_SENTINEL'),('s','user',nonce),('s','assistant',nonce)])
    with patch.object(sys, 'argv', ['verifier', str(home), str(target), str(evidence)]), patch('interrupted_recovery.observe', return_value=final):
        runpy.run_path(str(PROOF / 'interrupted_chat_verify.py'), run_name='__main__')
    receipt = json.loads((evidence / 'interrupted-chat.json').read_text())
    assert receipt['module_import_pids'] == [4490,5608,5628,5641,5653,5668,5678]
    assert receipt['launch_accounting'] == 'pending'
    assert receipt['history_sentinel'] == 'STAGE_HISTORY_SENTINEL'
    for invalid in ('{', '{"pid": 5628, "modules": []}', '{"pid": 9, "modules": {"gateway.run": "x"}}'):
        (evidence / 'live-modules-5628.json').write_text(invalid)
        (evidence / 'interrupted-chat.json').unlink(missing_ok=True)
        with patch.object(sys, 'argv', ['verifier', str(home), str(target), str(evidence)]), patch('interrupted_recovery.observe', return_value=final):
            with pytest.raises(ValueError, match='invalid live module observation'):
                runpy.run_path(str(PROOF / 'interrupted_chat_verify.py'), run_name='__main__')
        assert not (evidence / 'interrupted-chat.json').exists()
    record = next(r for r in json.loads(HOSTED.read_text()) if r['pid'] == 5628)
    (evidence / 'live-modules-5628.json').write_text(json.dumps(record))
    argv = ['verifier', str(home), str(target), str(evidence)]
    (evidence / 'chat-post-reply.json').write_text(json.dumps({'messages':['wrong']}))
    with patch.object(sys, 'argv', argv), patch('interrupted_recovery.observe', return_value=final), pytest.raises(ValueError, match='fresh nonce'):
        runpy.run_path(str(PROOF / 'interrupted_chat_verify.py'), run_name='__main__')
    (evidence / 'chat-post-reply.json').write_text(json.dumps({'messages':[nonce]}))
    (home / 'config.yaml').write_text('modified')
    with patch.object(sys, 'argv', argv), patch('interrupted_recovery.observe', return_value=final), pytest.raises(ValueError, match='configuration changed'):
        runpy.run_path(str(PROOF / 'interrupted_chat_verify.py'), run_name='__main__')
    (home / 'config.yaml').write_text('fixture')
    with sqlite3.connect(home / 'state.db') as db:
        db.execute("update messages set session_id='other' where content like 'RECOVERY_NONCE_%'")
    with patch.object(sys, 'argv', argv), patch('interrupted_recovery.observe', return_value=final), patch('time.monotonic', side_effect=[0, 121]), pytest.raises(ValueError, match='same session'):
        runpy.run_path(str(PROOF / 'interrupted_chat_verify.py'), run_name='__main__')
    assert not (evidence / 'interrupted-chat.json').exists()
