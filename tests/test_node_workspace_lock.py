import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from clover_cli import node_workspace as nw


def test_scope_and_reentrancy(tmp_path):
    (tmp_path / '.git').mkdir()
    (tmp_path / 'package.json').write_text('{}')
    child = tmp_path / 'web'
    child.mkdir()
    (child / 'package.json').write_text('{}')
    assert nw.workspace_root(child) == tmp_path
    with nw.npm_workspace_lock(tmp_path):
        with nw.npm_workspace_lock(child, timeout=.1):
            pass


def test_alias_and_workspace_members_share_canonical_lock(tmp_path):
    root = tmp_path / 'root'
    member = root / 'apps' / 'web'
    member.mkdir(parents=True)
    (root / 'package.json').write_text('{"workspaces":["apps/*"]}')
    (member / 'package.json').write_text('{}')
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    assert nw.workspace_root(alias / 'apps' / 'web') == root
    with nw.npm_workspace_lock(root):
        with nw.npm_workspace_lock(alias / 'apps' / 'web', timeout=.2):
            pass


def test_unsupported_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(nw, 'fcntl', None)
    monkeypatch.setattr(nw, 'msvcrt', None)
    with pytest.raises(RuntimeError):
        with nw.npm_workspace_lock(tmp_path):
            pytest.fail('entered without a lock')


def test_unlock_failure_closes_descriptor_and_clears_registry(tmp_path, monkeypatch):
    if nw.fcntl is None:
        pytest.skip('POSIX flock fault injection')
    real_flock = nw.fcntl.flock
    captured = []

    def broken_unlock(fd, operation):
        if operation == nw.fcntl.LOCK_UN:
            captured.append(fd)
            raise OSError('injected unlock failure')
        return real_flock(fd, operation)

    monkeypatch.setattr(nw.fcntl, 'flock', broken_unlock)
    with pytest.raises(OSError, match='injected unlock failure'):
        with nw.npm_workspace_lock(tmp_path):
            pass
    assert 'fd' not in nw._registry[nw.workspace_root(tmp_path)]
    with pytest.raises(OSError):
        os.fstat(captured[0])


def test_process_timeout_independence_and_crash_release(tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    (first / '.git').mkdir()
    (second / '.git').mkdir()
    ready = tmp_path / 'ready'
    code = 'from clover_cli.node_workspace import npm_workspace_lock; from pathlib import Path; import sys,time;\nwith npm_workspace_lock(Path(sys.argv[1])):\n Path(sys.argv[2]).touch(); time.sleep(60)'
    proc = subprocess.Popen([sys.executable, '-c', code, str(first), str(ready)])
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        marker = first / 'marker'
        with pytest.raises(TimeoutError):
            with nw.npm_workspace_lock(first, timeout=.2):
                marker.touch()
        assert not marker.exists()
        with nw.npm_workspace_lock(second, timeout=.2):
            pass
    finally:
        proc.kill()
        proc.wait(timeout=10)
    with nw.npm_workspace_lock(first, timeout=1):
        pass
    with pytest.raises(ValueError):
        with nw.npm_workspace_lock(first):
            raise ValueError()
    with nw.npm_workspace_lock(first, timeout=1):
        pass
