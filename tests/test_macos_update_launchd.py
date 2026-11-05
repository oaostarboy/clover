"""Concrete adapter tests; only OS inspection/launchctl seams are replaced."""
from dataclasses import replace
import json
from pathlib import Path
import plistlib

import pytest

from clover_cli import macos_update_launchd as m
from clover_cli.macos_update_lifecycle import LifecycleRefused


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path / 'install'
    root.mkdir()
    home = tmp_path / 'profile'
    home.mkdir()
    plist = tmp_path / 'gateway.plist'
    args = (str(root / '.venv/bin/python'), '-m', 'clover_cli.main', 'gateway', 'run')
    plist.write_bytes(plistlib.dumps({'Label': 'ai.clover.gateway',
        'ProgramArguments': list(args), 'WorkingDirectory': str(root),
        'EnvironmentVariables': {'CLOVER_HOME': str(home)}}))
    proc = m.Process(120, 1, 1001, args, root, {'CLOVER_HOME': str(home)})
    state = {'loaded': True, 'pid': 120, 'processes': {120: proc}, 'calls': []}
    def command(*args):
        state['calls'].append(args)
        if args[0] == 'print':
            if not state['loaded']:
                return m.CommandResult(113, '', 'Could not find service')
            return m.CommandResult(0, f'path = {plist}\npid = {state["pid"]}\n', '')
        return m.CommandResult(0, '', '')
    monkeypatch.setattr(m, '_launchctl', command)
    monkeypatch.setattr(m, '_process_table', lambda: state['processes'].copy())
    monkeypatch.setattr(m, '_protected_pids', lambda table: {999})
    def status(pid=120, birth=1001):
        (home / 'gateway_state.json').write_text(json.dumps({'kind':'clover-gateway',
            'pid':pid, 'start_time':birth, 'clover_home':str(home)}))
    status()
    service = m.Service(root, home, 'ai.clover.gateway', 'gui/501', plist,
                        plist.read_bytes(), True, 120, 1001)
    return m.LaunchdOperations(), service, state, status


def test_validate_and_exact_bootout(rig):
    ops, service, state, _ = rig
    ops.validate(service)
    ops.unload(service)
    assert state['calls'][-1] == ('bootout', 'gui/501/ai.clover.gateway')
    assert all(c[0] != 'kill' for c in state['calls'])


def test_wait_refuses_if_unloaded_gateway_process_is_still_running(rig):
    ops, service, state, _ = rig
    state['loaded'] = False
    with pytest.raises(LifecycleRefused, match='quiescent'):
        ops.wait_quiescent([service], [], 0)


def test_tracks_detached_builder_and_waits_without_signals(rig, monkeypatch):
    ops, service, state, _ = rig
    builder = m.Process(300, 1, 2001, ('node', '/usr/bin/npm', 'run', 'build'),
                        service.installation_root / 'apps/desktop', {})
    state['processes'][300] = builder
    children = ops.capture_builders([service])
    assert {p.pid for p in children} == {300}
    state['loaded'] = False
    state['processes'].pop(120)
    waits = []
    def finish_builder(seconds):
        waits.append(seconds)
        state['processes'].pop(300, None)
    monkeypatch.setattr(m.time, 'sleep', finish_builder)
    ops.wait_quiescent([service], children, 1)
    assert waits
    assert all(c[0] == 'print' for c in state['calls'])


def test_restore_unloaded_service_uses_saved_domain_and_proves_fresh_owner(rig, monkeypatch):
    ops, service, state, status = rig
    original = m._launchctl
    def command(*args):
        if args[0] == 'bootout':
            state['loaded'] = False
            state['processes'].clear()
        if args[0] == 'bootstrap':
            assert args == ('bootstrap', 'gui/501', str(service.plist))
            state['loaded'] = True
            state['pid'] = 121
            state['processes'][121] = m.Process(121, 1, 1002,
                (str(service.installation_root / '.venv/bin/python'), '-m',
                 'clover_cli.main', 'gateway', 'run'), service.installation_root,
                {'CLOVER_HOME': str(service.home)})
            status(121, 1002)
        return original(*args)
    monkeypatch.setattr(m, '_launchctl', command)
    ops.unload(service)
    ops.restore(service)
    calls = [c for c in state['calls'] if c[0] != 'print']
    assert calls == [('bootout', 'gui/501/ai.clover.gateway'),
                     ('bootstrap', 'gui/501', str(service.plist))]
    ops.restore(service)
    assert [c for c in state['calls'] if c[0] != 'print'] == calls


@pytest.fixture
def generated(rig, monkeypatch):
    from clover_cli import gateway
    ops, service, state, status = rig
    python = service.installation_root / '.venv/bin/python'
    python.parent.mkdir(parents=True)
    python.symlink_to('/usr/bin/python3')
    monkeypatch.setattr(gateway, 'get_clover_home', lambda: service.home)
    monkeypatch.setattr(gateway, 'get_python_path', lambda: str(python))
    monkeypatch.setattr(gateway, '_stable_service_working_dir', lambda: str(service.home))
    monkeypatch.setattr(gateway, 'get_launchd_label', lambda: service.label)
    monkeypatch.setattr(gateway, '_profile_arg', lambda: '')
    service.plist.write_text(gateway.generate_launchd_plist())
    args = tuple(plistlib.loads(service.plist.read_bytes())['ProgramArguments'])
    inner = args[args.index('--') + 1:]
    service = replace(service, plist_bytes=service.plist.read_bytes(), pid=121, birth=1002)
    service = replace(service, supervisor_pid=120, supervisor_birth=1001)
    state['processes'] = {120: m.Process(120, 1, 1001, args, service.home, {'CLOVER_HOME': str(service.home)}),
                          121: m.Process(121, 120, 1002, inner, service.home, {'CLOVER_HOME': str(service.home)})}
    status(121, 1002)
    return ops, service, state, status


def test_generated_launchd_wrapper_with_symlinked_venv(generated):
    ops, service, state, _ = generated
    ops.unload(service)
    assert state['calls'][-1][0] == 'bootout'


@pytest.mark.parametrize('mutation', ['foreign_child', 'wrapper_birth', 'child_birth',
    'wrapper_cwd', 'child_cwd', 'wrapper_home', 'child_home', 'wrapper_args',
    'child_args', 'plist', 'interpreter'])
def test_generated_service_refuses_unowned_process_before_bootout(generated, mutation):
    ops, service, state, _ = generated
    wrapper, child = state['processes'][120], state['processes'][121]
    if mutation == 'foreign_child': child = replace(child, ppid=1)
    if mutation == 'wrapper_birth': wrapper = replace(wrapper, birth=9999)
    if mutation == 'child_birth': child = replace(child, birth=9999)
    if mutation == 'wrapper_cwd': wrapper = replace(wrapper, cwd=service.installation_root)
    if mutation == 'child_cwd': child = replace(child, cwd=service.installation_root)
    if mutation == 'wrapper_home': wrapper = replace(wrapper, env={'CLOVER_HOME': '/foreign'})
    if mutation == 'child_home': child = replace(child, env={'CLOVER_HOME': '/foreign'})
    if mutation == 'wrapper_args': wrapper = replace(wrapper, argv=wrapper.argv + ('foreign',))
    if mutation == 'child_args': child = replace(child, argv=child.argv + ('foreign',))
    if mutation == 'plist': service.plist.write_bytes(service.plist_bytes + b' ')
    if mutation == 'interpreter': child = replace(child, argv=('/usr/bin/python3', *child.argv[1:]))
    state['processes'] = {120: wrapper, 121: child}
    with pytest.raises(LifecycleRefused): ops.unload(service)
    assert not any(call[0] == 'bootout' for call in state['calls'])


def test_restore_verifies_generated_wrapper_and_child(generated, monkeypatch):
    ops, service, state, status = generated
    original = m._launchctl
    wrapper, child = state['processes'][120], state['processes'][121]
    def command(*args):
        if args[0] == 'bootout':
            state['loaded'] = False
            state['processes'].clear()
        if args[0] == 'bootstrap':
            state['loaded'] = True
            state['pid'] = 130
            state['processes'] = {130: replace(wrapper, pid=130, birth=2001),
                                  131: replace(child, pid=131, ppid=130, birth=2002)}
            status(131, 2002)
        return original(*args)
    monkeypatch.setattr(m, '_launchctl', command)
    ops.unload(service)
    ops.restore(service)
    ops.restore(service)
    state['processes'][131] = replace(state['processes'][131], ppid=1)
    with pytest.raises(LifecycleRefused): ops.restore(service)


def test_wait_tracks_wrapper_after_child_exits(generated):
    ops, service, state, _ = generated
    state['loaded'] = False
    state['processes'].pop(121)
    with pytest.raises(LifecycleRefused, match='quiescent'):
        ops.wait_quiescent([service], [], 0)
