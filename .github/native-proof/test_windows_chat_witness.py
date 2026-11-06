import importlib.util
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location('witness', Path(__file__).with_name('windows_chat_witness.py'))
witness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(witness)


def test_same_session_requires_new_response_after_each_unique_user_turn():
    rows = [dict(id=1, session_id='a', role='user', content='PRE_123'),
            dict(id=2, session_id='a', role='assistant', content='Local acceptance auxiliary response.'),
            dict(id=3, session_id='a', role='user', content='POST_456'),
            dict(id=4, session_id='a', role='assistant', content='Local acceptance auxiliary response.')]
    assert witness.match_turns(rows, 'PRE_123', 'POST_456')['session_id'] == 'a'
    rows[-1]['session_id'] = 'b'
    assert witness.match_turns(rows, 'PRE_123', 'POST_456') is None
    rows[-1]['session_id'] = 'a'
    rows[-1]['role'] = 'user'
    assert witness.match_turns(rows, 'PRE_123', 'POST_456') is None


def test_irc_fixture_records_precise_utc_and_monotonic_time(tmp_path):
    server = Path(__file__).parent.parent / 'win-e2e' / 'fake_ircd.py'
    log = tmp_path / 'irc.log'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, str(server), str(log), str(tmp_path / 'trigger'), str(port)])
    try:
        for _ in range(100):
            if log.exists() and log.stat().st_size:
                break
            time.sleep(.02)
        assert re.match(r'\d{4}-\d\d-\d\dT\S+ mono=\d+\.\d+ -- listening', log.read_text())
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_receipt_precedes_wire_send():
    timeline = {'receipt_verified_at': '2026-01-01T00:00:00+00:00', 'trigger_at': '2026-01-01T00:00:01+00:00'}
    assert witness.ordered_after_receipt(timeline, '2026-01-01T00:00:02+00:00 mono=4 -> sent')
    assert not witness.ordered_after_receipt(timeline, '2025-12-31T23:59:59+00:00 mono=2 -> sent')


def test_wire_requires_reply_after_unique_sent_nonce():
    lines = ['-> :ant!ant@localhost PRIVMSG cloverbot :POST_456', '<< PRIVMSG ant :Local acceptance auxiliary response.']
    assert witness.match_wire(lines, 'POST_456') == lines
    assert witness.match_wire(lines[::-1], 'POST_456') is None
    assert witness.match_wire(lines + [lines[0]], 'POST_456') is None


def test_duplicate_nonce_and_stale_reply_are_rejected():
    rows = [dict(id=1, session_id='a', role='user', content='PRE_123'),
            dict(id=2, session_id='a', role='assistant', content=witness.REPLY),
            dict(id=3, session_id='a', role='user', content='POST_456'),
            dict(id=4, session_id='a', role='assistant', content=witness.REPLY)]
    assert witness.match_turns(rows + [dict(id=5, session_id='b', role='user', content='POST_456')], 'PRE_123', 'POST_456') is None
    assert witness.match_turns(rows[:3] + [dict(id=4, session_id='a', role='user', content='another turn'), rows[3]], 'PRE_123', 'POST_456') is None
