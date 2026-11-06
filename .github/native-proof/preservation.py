"""Private hosted fixture: product-API seed, read-only before/after witness."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import yaml

SESSION = 'native-update-preservation-witness'
USER = 'NATIVE_PRESERVATION_USER_WITNESS'
ASSISTANT = 'NATIVE_PRESERVATION_ASSISTANT_WITNESS'


def snapshot(home):
    config_bytes = (home / 'config.yaml').read_bytes()
    config = yaml.safe_load(config_bytes)
    if not isinstance(config, dict):
        raise ValueError('config is not a mapping')
    with sqlite3.connect((home / 'state.db').resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        session = [dict(row) for row in db.execute('SELECT * FROM sessions WHERE id=?', (SESSION,))]
        messages = [dict(row) for row in db.execute('SELECT * FROM messages WHERE session_id=? ORDER BY id', (SESSION,))]
    if len(session) != 1 or len(messages) != 2 or [(m['role'], m['content']) for m in messages] != [('user', USER), ('assistant', ASSISTANT)]:
        raise ValueError('missing or changed conversation witness')
    return {'config': config, 'config_sha256': hashlib.sha256(config_bytes).hexdigest(),
            'sessions': session, 'messages': messages}


def existing_values(before, after, path='config'):
    if isinstance(before, dict):
        if not isinstance(after, dict):
            raise ValueError('changed ' + path)
        for key, value in before.items():
            if key not in after:
                raise ValueError('missing ' + path + '.' + str(key))
            if path == 'config' and key == '_config_version':
                if type(value) is not int or type(after[key]) is not int or after[key] < value:
                    raise ValueError('invalid config schema migration')
            else:
                existing_values(value, after[key], path + '.' + str(key))
    elif type(before) is not type(after) or before != after:
        raise ValueError('changed ' + path)


def verify(before, after):
    existing_values(before['config'], after['config'])
    for table in ('sessions', 'messages'):
        old_rows, new_rows = before[table], after[table]
        if len(old_rows) != len(new_rows):
            raise ValueError('changed witness row count: ' + table)
        for old, new in zip(old_rows, new_rows):
            existing_values(old, new, table)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('seed', 'verify'))
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise SystemExit('disposable hosted runner required')
    if args.home.resolve() != Path(os.environ.get('CLOVER_HOME', '/nonexistent')).resolve():
        raise SystemExit('fixture home mismatch')
    args.evidence.mkdir(parents=True, exist_ok=True)
    if args.mode == 'seed':
        sys.path.insert(0, str(args.repo.resolve()))
        import clover_state
        if not Path(clover_state.__file__).resolve().is_relative_to(args.repo.resolve()):
            raise RuntimeError('wrong installed storage source')
        with clover_state.SessionDB(args.home / 'state.db') as db:
            if db.get_session(SESSION) is not None:
                raise RuntimeError('witness already exists; refusing repeated seed')
            db.create_session(SESSION, source='native-proof')
            db.append_message(SESSION, 'user', USER)
            db.append_message(SESSION, 'assistant', ASSISTANT)
        data = snapshot(args.home)
        output = args.evidence / 'preservation-before.json'
    else:
        before = json.loads((args.evidence / 'preservation-before.json').read_text())
        data = snapshot(args.home)
        # Retain actual after-state even if verification fails.
        (args.evidence / 'preservation-after.json').write_text(json.dumps(data, indent=2))
        verify(before, data)
        output = args.evidence / 'preservation-verified.json'
        data = {'passed': True, 'witness_session': SESSION, 'witness_messages': len(data['messages']),
                'config_bytes_equal': before['config_sha256'] == data['config_sha256'],
                'existing_settings_preserved': True,
                'allowed_config_changes': 'new defaults and monotonic top-level integer _config_version'}
    output.write_text(json.dumps(data, indent=2))


if __name__ == '__main__':
    main()
