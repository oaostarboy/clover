"""Fail-closed launchd operations for macos_update_lifecycle.

No signals, forced kills, plist regeneration or detached restart fallback.
OS seams stay small so ownership decisions run unchanged in portable tests.
"""
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import time

from clover_cli.macos_update_lifecycle import LifecycleRefused


@dataclass(frozen=True)
class Service:
    installation_root: Path
    home: Path
    label: str
    domain: str | None
    plist: Path
    plist_bytes: bytes
    loaded: bool
    pid: int | None
    birth: int | None
    supervisor_pid: int | None = None
    supervisor_birth: int | None = None


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    birth: int
    argv: tuple[str, ...]
    cwd: Path | None
    env: dict[str, str] | None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _launchctl(*args):
    result = subprocess.run(['launchctl', *args], capture_output=True,
                            text=True, timeout=10, stdin=subprocess.DEVNULL)
    return CommandResult(result.returncode, result.stdout, result.stderr)


def _process_table():
    import psutil
    from gateway.status import get_process_start_time
    table = {}
    for p in psutil.process_iter():
        try:
            birth = get_process_start_time(p.pid)
            if p.status() == psutil.STATUS_ZOMBIE:
                continue
            try:
                cwd = Path(p.cwd()).resolve()
                env = p.environ()
            except psutil.AccessDenied:
                cwd, env = None, None
            row = Process(p.pid, p.ppid(), birth, tuple(p.cmdline()), cwd, env)
            if birth is None or get_process_start_time(p.pid) != birth:
                raise LifecycleRefused('Process identity changed during inspection')
            table[p.pid] = row
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            # Cannot classify an unreadable process as unrelated.
            raise LifecycleRefused('Process ownership is unavailable') from exc
    return table


def _protected_pids(table):
    result = set()
    pid = os.getpid()
    while pid and pid not in result:
        result.add(pid)
        row = table.get(pid)
        pid = row.ppid if row else 0
    return result


def _inside(path, root):
    return path is not None and Path(path).resolve().is_relative_to(root)


def _gateway_args(argv, root):
    if not argv or not isinstance(argv[0], str):
        return False
    executable = Path(argv[0])
    if (not executable.is_absolute() or executable.name not in ('python', 'python3')
            or executable.parent.name != 'bin'
            or executable.parent.parent.name not in ('.venv', 'venv')
            or not _inside(executable.parent, root)):
        return False
    rest = list(argv[1:])
    if rest[:2] != ['-m', 'clover_cli.main']:
        return False
    rest = rest[2:]
    if len(rest) >= 2 and rest[0] == '--profile' and rest[1]:
        rest = rest[2:]
    return rest in (['gateway', 'run'], ['gateway', 'run', '--external-supervisor'])


def _commands(service):
    data = plistlib.loads(service.plist_bytes)
    args = tuple(data.get('ProgramArguments', ()))
    if len(args) >= 7 and args[1:4] == ('-m', 'clover_cli.stderr_timestamp', '--error-log') and args[5] == '--':
        inner = args[6:]
        if (not _gateway_args(inner, service.installation_root)
                or args[0] != inner[0]
                or Path(args[4]).resolve() != service.home / 'logs/gateway.error.log'
                or inner[-1] != '--external-supervisor'):
            raise LifecycleRefused('Service command is unknown')
        return args, inner
    if not _gateway_args(args, service.installation_root):
        raise LifecycleRefused('Service command is unknown')
    return args, args


def _registration(service):
    if service.domain is None:
        return False, None
    result = _launchctl('print', f'{service.domain}/{service.label}')
    if result.returncode:
        if result.returncode == 113 and 'Could not find service' in result.stderr:
            return False, None
        raise LifecycleRefused('Cannot inspect launchd registration: ' + result.stderr)
    path = re.search(r'^\s*path = (.+)$', result.stdout, re.M)
    if not path or Path(path[1]).resolve() != service.plist:
        raise LifecycleRefused('launchd plist identity differs')
    pid = re.search(r'^\s*pid = (\d+)\s*$', result.stdout, re.M)
    return True, int(pid[1]) if pid else None


def _verify_plist(service):
    if service.plist.read_bytes() != service.plist_bytes:
        raise LifecycleRefused('Service plist changed')
    data = plistlib.loads(service.plist_bytes)
    root = service.installation_root
    _commands(service)
    if (data.get('Label') != service.label
            or Path(data.get('WorkingDirectory', '/')).resolve() not in (root, service.home)
            or Path(data.get('EnvironmentVariables', {}).get('CLOVER_HOME', '/')).resolve() != service.home):
        raise LifecycleRefused('Service installation or profile is unknown')


def _verify_owner(service, pid, birth, table):
    p = table.get(pid)
    if not p or p.birth != birth or pid in _protected_pids(table):
        raise LifecycleRefused('Gateway PID birth changed or is updater ancestor')
    expected_wrapper, expected_inner = _commands(service)
    cwd = Path(plistlib.loads(service.plist_bytes)['WorkingDirectory']).resolve()
    supervisor_pid = service.supervisor_pid if service.supervisor_pid is not None else pid
    supervisor_birth = service.supervisor_birth if service.supervisor_pid is not None else birth
    supervisor = table.get(supervisor_pid)
    if (not supervisor or supervisor.birth != supervisor_birth
            or supervisor_pid in _protected_pids(table)
            or supervisor.argv != expected_wrapper or supervisor.cwd != cwd
            or not supervisor.env or Path(supervisor.env.get('CLOVER_HOME', '/')).resolve() != service.home):
        raise LifecycleRefused('Supervisor ownership is unknown')
    if (p.cwd != cwd or p.argv != expected_inner
            or pid not in _collect_children(table, {supervisor_pid})
            or (expected_wrapper != expected_inner and pid == supervisor_pid)
            or not p.env or Path(p.env.get('CLOVER_HOME', '/')).resolve() != service.home):
        raise LifecycleRefused('Gateway installation ownership is unknown')
    try:
        status = json.loads((service.home / 'gateway_state.json').read_text())
        valid = (status.get('kind') == 'clover-gateway' and status.get('pid') == pid
                 and status.get('start_time') == birth
                 and Path(status.get('clover_home', '/')).resolve() == service.home)
    except (OSError, ValueError, TypeError):
        valid = False
    if not valid:
        raise LifecycleRefused('Gateway profile status ownership differs')


def _builder_matches(process, roots):
    names = {Path(arg).name for arg in process.argv if isinstance(arg, str)}
    node_tool = bool(names & {'node', 'node.exe', 'npm', 'npm-cli.js', 'npx',
                            'pnpm', 'yarn', 'esbuild', 'vite', 'electron-vite',
                            'electron-builder'})
    clover_builder = bool(names & {'clover', 'cli.py', 'clover_cli.main'}) and bool(
        names & {'gui', 'desktop', '--build-only', '--force-build'})
    if not (node_tool or clover_builder):
        return False
    if process.cwd is None:
        raise LifecycleRefused('Cannot establish a possible builder workspace')
    return any(_inside(process.cwd, root) or any(
        Path(arg).is_absolute() and _inside(arg, root) for arg in process.argv)
        for root in roots)


def _collect_children(table, seeds):
    found = set(seeds)
    while True:
        descendants = {p.pid for p in table.values() if p.ppid in found}
        if descendants <= found:
            return found
        found.update(descendants)


class LaunchdOperations:
    def __init__(self, *, restore_timeout=30.0):
        self.restore_timeout = restore_timeout
        self._attempted = set()
        self.restored = {}

    def validate(self, service):
        _verify_plist(service)
        loaded, pid = _registration(service)
        supervisor_pid = service.supervisor_pid if service.supervisor_pid is not None else service.pid
        if (loaded, pid) != (service.loaded, supervisor_pid):
            raise LifecycleRefused('launchd owner changed')
        if pid is not None:
            _verify_owner(service, service.pid, service.birth, _process_table())

    def capture_builders(self, services):
        table = _process_table()
        roots = {s.installation_root for s in services}
        seeds = {p.pid for p in table.values() if _builder_matches(p, roots)}
        descendants = _collect_children(table, seeds)
        protected = _protected_pids(table)
        if descendants & protected:
            raise LifecycleRefused('Builder is an updater ancestor')
        return [table[pid] for pid in descendants]

    def wait_quiescent(self, services, children, timeout):
        deadline = time.monotonic() + timeout
        tracked = {p.pid: p.birth for p in children}
        roots = {s.installation_root for s in services}
        while True:
            table = _process_table()
            for pid, birth in tracked.items():
                if pid in table and table[pid].birth != birth:
                    raise LifecycleRefused('Builder process identity changed')
            seeds = {pid for pid in tracked if pid in table}
            seeds.update(p.pid for p in table.values() if _builder_matches(p, roots))
            for pid in _collect_children(table, seeds):
                tracked[pid] = table[pid].birth
            if set(tracked) & _protected_pids(table):
                raise LifecycleRefused('Builder is an updater ancestor')
            registrations = [_registration(s) for s in services]
            for s in services:
                for pid, birth in ((s.pid, s.birth), (s.supervisor_pid, s.supervisor_birth)):
                    if pid in table and table[pid].birth != birth:
                        raise LifecycleRefused('Service process identity changed')
            gateways_alive = any(pid in table for s in services
                                 for pid in (s.pid, s.supervisor_pid) if pid is not None)
            if not seeds and not gateways_alive and not any(loaded for loaded, _ in registrations):
                return
            if time.monotonic() >= deadline:
                raise LifecycleRefused('Services or builders did not become quiescent')
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def unload(self, service):
        self.validate(service)
        if service.loaded:
            self._attempted.add(service)
            result = _launchctl('bootout', f'{service.domain}/{service.label}')
            if result.returncode:
                raise LifecycleRefused('launchd bootout failed: ' + result.stderr)

    def restore(self, service):
        if service not in self._attempted:
            return
        _verify_plist(service)
        loaded, pid = _registration(service)
        old_supervisor = service.supervisor_pid if service.supervisor_pid is not None else service.pid
        old_birth = service.supervisor_birth if service.supervisor_pid is not None else service.birth
        if service in self.restored:
            expected = self.restored[service]
            if not loaded or pid != expected.supervisor_pid:
                raise LifecycleRefused('Restored service ownership changed')
            if pid is not None:
                _verify_owner(expected, expected.pid, expected.birth, _process_table())
            return
        if loaded and pid == old_supervisor and pid is not None:
            _verify_owner(service, service.pid, service.birth, _process_table())
            return
        if not loaded:
            result = _launchctl('bootstrap', service.domain, str(service.plist))
            if result.returncode:
                raise LifecycleRefused('launchd bootstrap failed: ' + result.stderr)
        deadline = time.monotonic() + self.restore_timeout
        while True:
            loaded, pid = _registration(service)
            if loaded and service.pid is None and pid is None:
                self.restored[service] = replace(service, supervisor_pid=None)
                return
            table = _process_table()
            row = table.get(pid)
            if loaded and row and (pid, row.birth) != (old_supervisor, old_birth):
                try:
                    status = json.loads((service.home / 'gateway_state.json').read_text())
                    child_pid, child_birth = status['pid'], status['start_time']
                    expected = replace(service, pid=child_pid, birth=child_birth,
                                       supervisor_pid=pid, supervisor_birth=row.birth)
                    _verify_owner(expected, child_pid, child_birth, table)
                except (LifecycleRefused, OSError, ValueError, KeyError, TypeError):
                    # A newly started gateway can precede its status file.
                    pass
                else:
                    self.restored[service] = expected
                    return
            if time.monotonic() >= deadline:
                raise LifecycleRefused('Restored service has no verified supervised successor')
            time.sleep(0.1)
