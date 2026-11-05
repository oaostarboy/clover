"""Bounded, cross-process coordination for npm workspace mutations."""

from contextlib import contextmanager
import errno
import glob
import json
import os
from pathlib import Path
import threading
import time

try:
    import fcntl
except ImportError:
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None

_registry_guard = threading.Lock()
_registry = {}


def _after_fork():
    global _registry_guard, _registry
    for state in _registry.values():
        fd = state.get('fd')
        if fd is not None:
            os.close(fd)
    _registry_guard = threading.Lock()
    _registry = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def workspace_root(cwd):
    """Conservatively group package roots within the enclosing repository."""
    cwd = Path(cwd).resolve()
    ancestors = (cwd, *cwd.parents)
    repo = next((p for p in ancestors if (p / '.git').exists()), None)
    if repo is None:
        for path in ancestors[1:]:
            try:
                data = json.loads((path / 'package.json').read_text(encoding='utf-8'))
                patterns = data.get('workspaces', [])
                if isinstance(patterns, dict):
                    patterns = patterns.get('packages', [])
                for pattern in patterns:
                    if not isinstance(pattern, str) or pattern.startswith('!'):
                        continue
                    for match in glob.glob(str(path / pattern)):
                        member = Path(match).resolve()
                        if cwd == member or member in cwd.parents:
                            return path
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        return cwd
    candidates = []
    for path in ancestors:
        if (path / 'package.json').is_file():
            candidates.append(path)
        if path == repo:
            break
    return candidates[-1] if candidates else (repo or cwd)


@contextmanager
def npm_workspace_lock(cwd, timeout=600):
    """Lock a canonical workspace; raise TimeoutError rather than run unlocked."""
    if fcntl is None and msvcrt is None:
        raise RuntimeError('No supported cross-process file locking available')
    root = workspace_root(cwd)
    with _registry_guard:
        state = _registry.setdefault(root, {'lock': threading.RLock(), 'depth': 0})
    deadline = time.monotonic() + timeout
    if not state['lock'].acquire(timeout=max(0, timeout)):
        raise TimeoutError(f'Timed out waiting for npm workspace lock: {root}')
    try:
        if state['depth']:
            state['depth'] += 1
            try:
                yield
            finally:
                state['depth'] -= 1
            return
        path = root / '.clover-npm-workspace.lock'
        flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags, 0o600)
        state['fd'] = fd
        os.set_inheritable(fd, False)
        acquired = False
        try:
            while True:
                try:
                    if fcntl is not None:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    else:
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f'Timed out waiting for npm workspace lock: {root}') from exc
                    time.sleep(min(.05, remaining))
            state['depth'] = 1
            try:
                yield
            finally:
                state['depth'] = 0
        finally:
            try:
                if acquired:
                    if fcntl is not None:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    else:
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            finally:
                state.pop('fd', None)
                os.close(fd)
    finally:
        state['lock'].release()
