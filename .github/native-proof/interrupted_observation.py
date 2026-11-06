"""Fence and classify an owned tracer's bounded watcher restart window."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import time

import psutil
import interrupted_identity as identity
from interrupted_identity import Refusal
from interrupted_launch_accounting import read_process_tree, verify_single_recovery_launch


_TERMINAL = re.compile(rb'(?:\d+\.\d+\s+)?\+\+\+ (?:exited with \d+|killed by SIG[A-Z0-9]+(?: \(core dumped\))?) \+\+\+\n$')


def bind_successor(state_path, home, target):
    """Bind the after-state PID to a live, unchanged private process."""
    try:
        path = Path(state_path)
        raw = path.read_bytes()
        if raw != path.read_bytes():
            raise Refusal('after-state changed while binding successor')
        state = json.loads(raw)
        pid = state['pid']
        if type(pid) is not int or pid <= 1:
            raise Refusal('invalid successor PID')
        proc = psutil.Process(pid)
        def sample():
            return (identity.birth(pid), proc.cmdline(), proc.exe(), proc.cwd(),
                    proc.environ().get('CLOVER_HOME'), proc.uids().real, proc.status())
        first = sample()
        if first != sample():
            raise Refusal('successor changed while binding')
        birth, argv, exe, cwd, actual_home, uid, status = first
        if (str(birth) != str(state['start_time']) or not argv
                or exe != str((Path(target) / 'venv/bin/python').resolve(strict=True))
                or cwd != str(target) or actual_home != str(home) or uid != os.getuid()
                or status == psutil.STATUS_ZOMBIE):
            raise Refusal('after-state does not identify owned successor')
        return {'pid': pid, 'birth_ticks': birth, 'argv': argv, 'exe': exe,
                'after_state_sha256': hashlib.sha256(raw).hexdigest()}
    except (OSError, ValueError, KeyError, TypeError, psutil.Error) as exc:
        raise Refusal('cannot bind after-state successor') from exc


def _assert_traced(pid, tracer_pid):
    try:
        status = Path('/proc', str(pid), 'status').read_text(encoding='ascii')
        matches = re.findall(r'^TracerPid:\s*(\d+)\s*$', status, re.MULTILINE)
    except OSError as exc:
        raise Refusal('successor not attached to owned tracer') from exc
    if matches != [str(tracer_pid)]:
        raise Refusal('successor not attached to owned tracer')


def _await_watchers(session, watchers, timeout):
    deadline = time.monotonic() + timeout
    for data in watchers.values():
        pid, birth = data['pid'], data['birth_ticks']
        while True:
            if session.tracer.poll() is not None:
                raise Refusal('tracer exited while awaiting watchers')
            try:
                actual_birth = identity.birth(pid)
                if actual_birth != birth:
                    raise Refusal('watcher PID was reused')
                dead = identity.state(pid) in ('Z', 'X')
            except Refusal:
                if Path('/proc', str(pid)).exists():
                    raise
                dead = True
            path = Path(str(session.prefix) + '.' + str(pid))
            try:
                raw = path.read_bytes()
                terminal = bool(_TERMINAL.search(raw))
            except FileNotFoundError:
                terminal = False
            if dead and terminal:
                break
            if time.monotonic() >= deadline:
                raise Refusal('watcher did not terminate with kernel evidence')
            time.sleep(.01)


def _check_stderr(raw, pids, binary='strace'):
    if raw and not raw.endswith(b'\n'):
        raise Refusal('partial tracer stderr')
    attached = []
    prefix = re.escape(os.fsencode(binary))
    for line in raw.splitlines():
        match = re.fullmatch(prefix + rb': Process (\d+) attached', line)
        if not match or int(match[1]) not in pids:
            raise Refusal('tracer error, detach or unaccounted attachment')
        attached.append(int(match[1]))
    if len(attached) != len(set(attached)):
        raise Refusal('duplicate tracer attachment')
    return attached


def _snapshot(session, destination, timeout):
    if session.tracer.poll() is not None:
        raise Refusal('tracer is not alive at observation fence')
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    identity.signal_bound(session.tracer.pid, session.tracer_start_ticks, signal.SIGSTOP)
    try:
        deadline = time.monotonic() + timeout
        while identity.state(session.tracer.pid) not in ('T', 't'):
            if session.tracer.poll() is not None or time.monotonic() >= deadline:
                raise Refusal('tracer failed to stop at observation fence')
            time.sleep(.005)
        cut = time.time_ns()
        prefix = session.prefix
        def paths():
            return sorted(p for p in prefix.parent.glob(prefix.name + '.*')
                          if p.name.rsplit('.', 1)[1].isdigit())
        files = paths()
        if not files:
            raise Refusal('empty trace snapshot')
        stderr = Path(str(prefix) + '.stderr')
        stderr_bytes = stderr.read_bytes()
        trace_pids = {int(p.name.rsplit('.', 1)[1]) for p in files}
        attached = _check_stderr(stderr_bytes, trace_pids, session.binary)
        if set(attached) != trace_pids - {session.updater.pid}:
            raise Refusal('missing tracer attachment evidence')
        hashes = {}
        for path in files:
            if path.is_symlink():
                raise Refusal('symlink in trace snapshot')
            before = path.stat()
            raw = path.read_bytes()
            after = path.stat()
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            if signature(before) != signature(after) or raw != path.read_bytes():
                raise Refusal('trace changed while fenced')
            name = 'trace.' + path.name.rsplit('.', 1)[1]
            out = destination / name
            fd = os.open(out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400)
            with os.fdopen(fd, 'wb') as f:
                f.write(raw)
            hashes[name] = hashlib.sha256(raw).hexdigest()
        if (paths() != files or stderr.read_bytes() != stderr_bytes
                or identity.birth(session.tracer.pid) != session.tracer_start_ticks
                or identity.state(session.tracer.pid) not in ('T', 't')):
            raise Refusal('trace, tracer fence or stderr changed while fenced')
        stderr_out = destination / 'tracer.stderr'
        fd = os.open(stderr_out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400)
        with os.fdopen(fd, 'wb') as f:
            f.write(stderr_bytes)
    finally:
        if session.tracer.poll() is None:
            identity.signal_bound(session.tracer.pid, session.tracer_start_ticks, signal.SIGCONT)
    return destination / 'trace', {'cut_wall_ns': cut, 'files_sha256': hashes,
                                    'stderr_sha256': hashlib.sha256(stderr_bytes).hexdigest(),
                                    'attached_pids': attached,
                                    'fence': 'owned tracer stopped after watcher terminal records'}


def account(session, fault, gateway_pid, gateway_argv, utilities, destination, *, timeout=15):
    if session.updater.poll() is None:
        raise Refusal('updater still alive at accounting')
    watchers = fault['watchers']
    _await_watchers(session, watchers, timeout)
    try:
        gateway = psutil.Process(gateway_pid)
        gateway_birth = identity.birth(gateway_pid)
        if gateway.status() == psutil.STATUS_ZOMBIE or gateway.cmdline() != list(gateway_argv):
            raise Refusal('successor process identity mismatch')
        _assert_traced(gateway_pid, session.tracer.pid)
        prefix, snapshot = _snapshot(session, destination, timeout)
        _assert_traced(gateway_pid, session.tracer.pid)
        if identity.birth(gateway_pid) != gateway_birth or gateway.cmdline() != list(gateway_argv):
            raise Refusal('successor changed across snapshot')
    except (psutil.Error, OSError) as exc:
        raise Refusal('successor unavailable during observation') from exc
    tree = read_process_tree(prefix, successor_root=gateway_pid)
    if tree['root'] != session.updater.pid:
        raise Refusal('trace root is not the bound updater')
    if not any(e['pid'] == tree['root'] and e['kind'] == 'exit' for e in tree['events']):
        raise Refusal('updater terminal record missing')
    watcher_ids = [v['pid'] for v in watchers.values()]
    if len(set(watcher_ids)) != len(watcher_ids):
        raise Refusal('duplicate watcher identity')
    for pid in watcher_ids:
        events = [e for e in tree['events'] if e['pid'] == pid]
        if (tree['parents'].get(pid) != tree['root']
                or len([e for e in events if e['kind'] == 'exec']) != 1
                or len([e for e in events if e['kind'] == 'exit']) != 1):
            raise Refusal('watcher has incomplete or unexpected kernel history')
    summary = verify_single_recovery_launch(tree, watcher_ids,
                                            gateway_pid, gateway_argv, utilities=utilities)
    return {'accounting': summary, 'snapshot': snapshot, 'gateway_birth_ticks': gateway_birth,
            'empty_thread_tids': tree['empty_thread_tids'], 'empty_thread_edges': tree['empty_thread_edges']}
