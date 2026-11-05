import os
from types import SimpleNamespace

import psutil
import pytest

from clover_cli import macos_update_launchd as launchd
from clover_cli.macos_update_lifecycle import LifecycleRefused


class FakeProcess:
    def __init__(self, pid, real, effective, denied=None):
        self.pid, self.real, self.effective, self.denied = pid, real, effective, denied
        self.calls = []

    def _get(self, field, value):
        self.calls.append(field)
        if self.denied == field:
            raise psutil.AccessDenied(self.pid)
        if self.denied == 'gone' and field == 'uids':
            raise psutil.NoSuchProcess(self.pid)
        return value

    def uids(self): return self._get('uids', SimpleNamespace(real=self.real, effective=self.effective))
    def status(self): return self._get('status', psutil.STATUS_RUNNING)
    def cwd(self): return self._get('cwd', '/')
    def environ(self): return self._get('environ', {})
    def ppid(self): return self._get('ppid', 1)
    def cmdline(self): return self._get('cmdline', ['python'])


def snapshot(monkeypatch, processes):
    import gateway.status
    monkeypatch.setattr(psutil, 'process_iter', lambda: iter(processes))
    monkeypatch.setattr(gateway.status, 'get_process_start_time', lambda pid: 42)
    return launchd._process_table()


def test_foreign_principal_is_not_inspected(monkeypatch):
    uid = os.geteuid()
    foreign = FakeProcess(1, uid + 1, uid + 1, 'cmdline')
    owned = FakeProcess(2, uid, uid)
    table = snapshot(monkeypatch, [foreign, owned])
    assert 1 not in table
    assert 'cmdline' not in foreign.calls
    assert table[2].argv == ('python',)


@pytest.mark.parametrize('real,effective,denied', [
    (0, 0, 'uids'),
    (1, 1, 'cmdline'),
    (0, 1, 'cmdline'),
    (1, 0, 'ppid'),
])
def test_unreadable_identity_or_owned_process_refuses(monkeypatch, real, effective, denied):
    uid = os.geteuid()
    p = FakeProcess(7, uid if real else uid + 1, uid if effective else uid + 1, denied)
    with pytest.raises(LifecycleRefused):
        snapshot(monkeypatch, [p])


def test_disappeared_process_is_benign(monkeypatch):
    p = FakeProcess(7, 0, 0, 'gone')
    assert snapshot(monkeypatch, [p]) == {}
