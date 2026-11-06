"""Read-only local IRC session witness for the disposable Windows fixture."""
import datetime
import json
from pathlib import Path
import sqlite3
import sys
import time

REPLY = 'Local acceptance auxiliary response.'


def match_turns(rows, before, after):
    if not before or not after or before == after:
        return None
    for marker in (before, after):
        if sum(row.get('role') == 'user' and marker in str(row.get('content')) for row in rows) != 1:
            return None
    matches = []
    for marker in (before, after):
        found = None
        for i, row in enumerate(rows):
            if row.get('role') != 'user' or marker not in str(row.get('content')):
                continue
            for following in rows[i + 1:]:
                if following.get('session_id') != row.get('session_id'):
                    continue
                if following.get('role') == 'user':
                    break
                if following.get('role') == 'assistant' and REPLY in str(following.get('content')):
                    found = (row, following)
                    break
            if found:
                break
        if not found:
            return None
        matches.append(found)
    if matches[0][0]['session_id'] != matches[1][0]['session_id']:
        return None
    if rows.index(matches[0][1]) >= rows.index(matches[1][0]):
        return None
    return {'session_id': matches[0][0]['session_id'], 'turns': matches}


def match_wire(lines, marker):
    sent = [i for i, line in enumerate(lines) if '-> ' in line and ' PRIVMSG ' in line and marker in line]
    if len(sent) != 1:
        return None
    for line in lines[sent[0] + 1:]:
        if '-> ' in line and ' PRIVMSG ' in line:
            break
        if '<< PRIVMSG ant :' in line and REPLY in line:
            return [lines[sent[0]], line]
    return None


def ordered_after_receipt(timeline, sent_line):
    parse = datetime.datetime.fromisoformat
    return parse(timeline['receipt_verified_at']) <= parse(timeline['trigger_at']) <= parse(sent_line.split()[0])


def main():
    home, evidence, before, after, irc_log = sys.argv[1:]
    dbpath = Path(home) / 'state.db'
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            with sqlite3.connect(f'file:{dbpath.as_posix()}?mode=ro', uri=True) as db:
                db.row_factory = sqlite3.Row
                rows = [dict(row) for row in db.execute('select * from messages order by id')]
            matched = match_turns(rows, before, after)
            wire = match_wire(Path(irc_log).read_text(encoding='utf-8').splitlines(), after)
            if matched and wire:
                timeline = json.loads((Path(evidence) / 'post-receipt-timeline.json').read_text(encoding='utf-8-sig'))
                if not ordered_after_receipt(timeline, wire[0]):
                    raise RuntimeError('wire send did not follow receipt verification and trigger')
                output = {'observed_at_unix': time.time(), 'monotonic': time.monotonic(), 'wire_lines': wire, **matched}
                (Path(evidence) / 'post-receipt-session.json').write_text(json.dumps(output, default=str, indent=2), encoding='utf-8')
                return
        except (sqlite3.Error, OSError):
            pass
        time.sleep(.25)
    raise RuntimeError('unique messages and replies not found in the same session')


if __name__ == '__main__':
    main()
