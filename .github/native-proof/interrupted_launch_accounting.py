"""Strict process-only strace decoding. Unknown records fail closed.

This parses evidence, not authority to signal processes. A parsed tree alone
is not a completeness certificate: its owner must prove tracer lifetime and
freeze/copy the observation boundary before classification.
"""
import ast
from decimal import Decimal
from pathlib import Path
import re
from interrupted_identity import Refusal


_STRING = r'"(?:[^"\\]|\\.)*"'


def _exec_arguments(call, name):
    # execveat includes a dirfd; unknown variants intentionally refuse.
    body = call[len(name) + 1:]
    if name == 'execveat':
        body = body.split(', ', 1)[1]
    match = re.match('(' + _STRING + r'), (\[)', body)
    if not match:
        raise Refusal('missing or truncated exec arguments')
    path = ast.literal_eval(match[1])
    start = match.end() - 1
    quoted = escaped = False
    end = None
    for i in range(start + 1, len(body)):
        char = body[i]
        if escaped:
            escaped = False
        elif quoted and char == '\\':
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted and char == ']':
            end = i + 1
            break
    if end is None or not body[end:].startswith(', '):
        raise Refusal('unterminated exec argv')
    try:
        argv = ast.literal_eval(body[start:end])
    except (ValueError, SyntaxError) as exc:
        raise Refusal('invalid or truncated exec argv') from exc
    if not isinstance(argv, list) or not all(isinstance(v, str) for v in argv):
        raise Refusal('non-string or abbreviated exec argv')
    return path, argv


def verify_single_recovery_launch(tree, watcher_pids, gateway_pid, gateway_argv, *, utilities=()):
    """Account for watcher-created branches, excluding gateway-owned work.

    The caller supplies independently bound watcher and successor identities.
    This is not a claim that the gateway itself never spawns tools/children.
    """
    watchers = set(watcher_pids)
    parents = tree['parents']
    all_pids = set(parents) | {tree['root']}
    if not watchers or not watchers <= all_pids or gateway_pid in watchers:
        raise Refusal('watcher identities absent or overlapping gateway')
    def below(pid, seeds):
        seen = set()
        while pid not in seeds:
            if pid in seen or pid not in parents:
                return False
            seen.add(pid)
            pid = parents[pid]
        return True
    if not below(gateway_pid, watchers):
        raise Refusal('gateway is not a traced watcher descendant')
    branches = {pid for pid in all_pids if pid not in watchers and below(pid, watchers)
                and (pid == gateway_pid or not below(pid, {gateway_pid}))}
    utility_pids = set()
    counts = [0] * len(utilities)
    for rule in utilities:
        if (rule.get('parent_pid') not in watchers
                or not isinstance(rule.get('path'), str) or not Path(rule['path']).is_absolute()
                or not isinstance(rule.get('argv'), list)
                or not all(isinstance(arg, str) for arg in rule['argv'])
                or type(rule.get('max_count')) is not int or rule['max_count'] < 1):
            raise Refusal('invalid source-bound utility rule')
    for pid in branches - {gateway_pid}:
        records = [e for e in tree['events'] if e['pid'] == pid]
        execs = [e for e in records if e['kind'] == 'exec']
        if (len(execs) != 1 or pid in parents.values()
                or not any(e['kind'] == 'exit' for e in records)):
            raise Refusal('unexplained, live, or descendant-spawning utility branch')
        event = execs[0]
        matches = [i for i, rule in enumerate(utilities)
                   if parents[pid] == rule['parent_pid']
                   and event.get('path') == rule['path'] and event['argv'] == rule['argv']]
        if len(matches) != 1:
            raise Refusal('unexpected or ambiguously classified utility execution')
        index = matches[0]
        counts[index] += 1
        if counts[index] > utilities[index]['max_count']:
            raise Refusal('source-bound utility count exceeded')
        utility_pids.add(pid)
    launches = [e for e in tree['events'] if e['kind'] == 'exec'
                and e['pid'] in branches - utility_pids]
    if (len(launches) != 1 or launches[0]['pid'] != gateway_pid
            or launches[0]['argv'] != list(gateway_argv)):
        raise Refusal('missing, duplicate or unexpected recovery execution')
    return {'gateway_pid': gateway_pid, 'launch_count': 1,
            'utility_pids': sorted(utility_pids), 'utility_counts': counts,
            'scope': 'watcher branches excluding successor-owned descendants'}


def read_process_tree(prefix):
    prefix = Path(prefix)
    paths = {int(p.name.rsplit('.', 1)[1]): p for p in prefix.parent.glob(prefix.name + '.*')
             if p.name.rsplit('.', 1)[1].isdigit()}
    if not paths:
        raise Refusal('no process traces')
    events, parents = [], {}
    for pid, path in sorted(paths.items()):
        text = path.read_text(encoding='ascii')
        if not text or not text.endswith('\n'):
            raise Refusal('empty or partial process trace')
        pending = None
        previous = -1
        for raw in text.splitlines():
            stamp = re.fullmatch(r'(\d+\.\d+)\s+(.*)', raw)
            if not stamp:
                raise Refusal('missing process event timestamp')
            ns = int(Decimal(stamp[1]) * 1000000000)
            if ns < previous:
                raise Refusal('process trace clock moved backwards')
            previous = ns
            line = stamp[2]
            if line.endswith('<unfinished ...>'):
                if pending is not None:
                    raise Refusal('nested unfinished syscall')
                pending = (ns, line[:-len('<unfinished ...>')])
                continue
            resumed = re.match(r'<\.\.\. (\w+) resumed>(.*)', line)
            if resumed:
                if pending is None or not pending[1].startswith(resumed[1] + '('):
                    raise Refusal('unmatched resumed syscall')
                ns, beginning = pending
                line, pending = beginning + resumed[2], None
            if line.startswith('--- '):
                if not line.endswith(' ---'):
                    raise Refusal('partial signal record')
                continue
            if line.startswith('+++ '):
                if not re.fullmatch(r'\+\+\+ (?:exited with \d+|killed by SIG[A-Z0-9]+(?: \(core dumped\))?) \+\+\+', line):
                    raise Refusal('unknown process terminal record')
                events.append({'pid': pid, 'ns': ns, 'kind': 'exit'})
                continue
            call = re.fullmatch(r'(\w+)\((.*)\)\s+=\s+(.+)', line)
            if not call:
                raise Refusal('unparsed process syscall')
            name, result = call[1], call[3]
            if name in ('execve', 'execveat'):
                if result == '0':
                    executable, argv = _exec_arguments(line, name)
                    events.append({'pid': pid, 'ns': ns, 'kind': 'exec',
                                   'path': executable, 'argv': argv})
                elif not re.match(r'-1 [A-Z0-9]+(?: |$)', result):
                    raise Refusal('unknown exec outcome')
            elif name in ('clone', 'clone3', 'fork', 'vfork'):
                if result.isdigit() and int(result) > 0:
                    child = int(result)
                    if child in parents or child == pid:
                        raise Refusal('duplicate or cyclic process creation')
                    parents[child] = pid
                    events.append({'pid': pid, 'ns': ns, 'kind': 'fork',
                                   'child': child, 'thread': 'CLONE_THREAD' in call[2]})
                elif not re.match(r'-1 [A-Z0-9]+(?: |$)', result):
                    raise Refusal('unknown fork outcome')
            elif name not in ('wait4', 'waitid', 'waitpid', 'exit', 'exit_group'):
                raise Refusal('unrecognized process syscall: ' + name)
        if pending is not None:
            raise Refusal('unfinished process syscall at observation boundary')
    if set(parents) - set(paths):
        raise Refusal('created process has no trace')
    roots = set(paths) - set(parents)
    if len(roots) != 1:
        raise Refusal('trace tree does not have one root')
    root = roots.pop()
    for pid in paths:
        seen = set()
        while pid != root:
            if pid in seen or pid not in parents:
                raise Refusal('disconnected or cyclic process tree')
            seen.add(pid)
            pid = parents[pid]
    return {'root': root, 'parents': parents,
            'events': sorted(events, key=lambda e: (e['ns'], e['pid']))}
