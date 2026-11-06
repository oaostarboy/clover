"""Fixture-only traced entry. Not a standalone or production updater."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import socket
import stat
import fcntl
import sys


def source_matches(actual, pinned, digest, alias=None):
    actual_path = Path(actual)
    if not actual_path.is_absolute() or str(actual_path.resolve()) != actual:
        return False
    if actual not in (pinned, alias):
        return False
    try:
        return hashlib.sha256(actual_path.read_bytes()).hexdigest() == digest
    except OSError:
        return False


def main():
    if not sys.flags.isolated or not sys.flags.no_site:
        raise RuntimeError('requires -I -S')
    if len(sys.argv) < 3 or sys.argv[2] != '--':
        raise RuntimeError('expected config -- entry arguments')
    cfg = json.loads(Path(sys.argv[1]).read_text())
    if 'stderr_fd' in cfg:
        fd = cfg['stderr_fd']
        if type(fd) is not int or fd <= 2 or fd == cfg.get('fd'):
            raise RuntimeError('invalid tracee stderr fd')
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or (fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE) == os.O_RDONLY):
            raise RuntimeError('unsafe tracee stderr fd')
        os.dup2(fd, 2)
        os.close(fd)
    files = {}
    for key in ('entry', 'gateway', 'caller'):
        path = Path(cfg[key])
        if not path.is_absolute() or path.resolve() != path:
            raise RuntimeError('noncanonical source')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != cfg[key + '_hash']:
            raise RuntimeError('source hash mismatch')
        files[key] = str(path)
    if str(Path(cfg['home']).resolve()) != cfg['home'] or os.environ.get('CLOVER_HOME') != cfg['home']:
        raise RuntimeError('home mismatch')
    channel = socket.socket(fileno=cfg['fd'])
    channel.settimeout(120)
    stream = channel.makefile('rwb', buffering=0)
    if stream.readline() != f'bind {os.getpid()}\n'.encode():
        raise RuntimeError('PID binding mismatch')
    expected = ['prepare', 'wait']
    def trace(frame, event, result):
        if event != 'return' or not expected:
            return trace
        # Observe the real Popen return, never replace its behavior. Only an
        # exact pinned product caller may attest a private child launch.
        if frame.f_code.co_name == '__init__' and frame.f_globals.get('__name__') == 'subprocess':
            owner = frame.f_back
            if owner is not None:
                owner_path = str(Path(owner.f_code.co_filename).resolve())
                for key, functions in cfg.get('launch_functions', {}).items():
                    if (owner.f_code.co_name in functions and source_matches(
                            owner_path, files[key], cfg[key + '_hash'], cfg.get(key + '_alias'))):
                        if hashlib.sha256(Path(owner_path).read_bytes()).hexdigest() != cfg[key + '_hash']:
                            raise RuntimeError('launch source changed')
                        child = frame.f_locals.get('self')
                        argv = getattr(child, 'args', None)
                        pid = getattr(child, 'pid', None)
                        if isinstance(pid, int) and pid > 1 and isinstance(argv, (list, tuple)):
                            stream.write((json.dumps({'stage': 'launch', 'pid': os.getpid(),
                                'child_pid': pid, 'argv': list(argv),
                                'owner': owner.f_code.co_name, 'owner_file': owner_path}) + '\n').encode())
                        break
        stage = expected[0]
        if frame.f_code.co_name != cfg[stage]:
            return trace
        caller = frame.f_back
        frame_path = str(Path(frame.f_code.co_filename).resolve())
        caller_path = str(Path(caller.f_code.co_filename).resolve()) if caller else ''
        if (caller is None or not source_matches(frame_path, files['gateway'], cfg['gateway_hash'], cfg.get('gateway_alias'))
            or not source_matches(caller_path, files['caller'], cfg['caller_hash'], cfg.get('caller_alias'))
            or caller.f_code.co_name != cfg['caller_function']):
            return trace
        for key in ('gateway', 'caller'):
            if hashlib.sha256(Path(files[key]).read_bytes()).hexdigest() != cfg[key + '_hash']:
                raise RuntimeError('source changed at barrier')
        if stage == 'prepare' and result not in ('detached', 'detached-cmdline'):
            raise RuntimeError('normal watcher not armed')
        expected.pop(0)
        stream.write((json.dumps({'stage': stage, 'pid': os.getpid(), 'result': result,
                                  'function': frame.f_code.co_name,
                                  'frame': frame_path, 'caller': caller_path}) + '\n').encode())
        if stream.readline() != b'continue\n':
            raise RuntimeError('controller did not release barrier')
        return trace
    sys.argv = [files['entry'], *sys.argv[3:]]
    sys.settrace(trace)
    try:
        runpy.run_path(files['entry'], run_name='__main__')
    finally:
        sys.settrace(None)
        stream.close()
        channel.close()
    if expected:
        raise RuntimeError('entry returned before both barriers')


if __name__ == '__main__':
    main()
