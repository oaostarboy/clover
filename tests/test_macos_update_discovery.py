from types import SimpleNamespace
from dataclasses import replace
import json
import plistlib
import pytest
from clover_cli import macos_update_launchd as m
from clover_cli import gateway, profiles


@pytest.fixture
def discovery(tmp_path, monkeypatch):
    root = tmp_path / 'install'; root.mkdir()
    home = tmp_path / 'home'; home.mkdir()
    plist = tmp_path / 'ai.clover.gateway.plist'
    label = 'ai.clover.gateway'
    args = (str(root / '.venv/bin/python'), '-m', 'clover_cli.main', 'gateway', 'run')
    plist.write_bytes(plistlib.dumps({'Label': label, 'ProgramArguments': list(args),
        'WorkingDirectory': str(root), 'EnvironmentVariables': {'CLOVER_HOME': str(home)}}))
    (home / 'gateway_state.json').write_text(json.dumps({'kind':'clover-gateway','pid':120,
        'start_time':1001, 'clover_home':str(home)}))
    state = {'domains': {'gui':120}, 'table': {120:m.Process(120, 1, 1001, args, root, {'CLOVER_HOME': str(home)})}}
    monkeypatch.setattr(profiles, 'list_profiles', lambda: [SimpleNamespace(name='default', path=home, is_default=True)])
    monkeypatch.setattr(gateway, 'launchd_gateway_labels_for_install', lambda: [label])
    monkeypatch.setattr(gateway, 'get_launchd_plist_path', lambda: plist)
    def launchctl(*args):
        domain = args[1].split('/')[0]
        if domain not in state['domains']:
            return m.CommandResult(113, '', 'Could not find service')
        pid = state['domains'][domain]
        return m.CommandResult(0, f'path = {plist}\n' + (f'pid = {pid}\n' if pid else ''), '')
    monkeypatch.setattr(m, '_launchctl', launchctl)
    monkeypatch.setattr(m, '_process_table', lambda: state['table'].copy())
    monkeypatch.setattr(m, '_protected_pids', lambda table: {999})
    return root, home, plist, state


def test_absent_launchd_services_are_not_invented(discovery):
    root, _, _, state = discovery
    state['domains'].clear(); state['table'].clear()
    assert m.discover_services(root) == []


@pytest.mark.parametrize('domain', ['gui', 'user'])
def test_discovers_owned_job_and_preserves_domain(discovery, domain):
    root, home, plist, state = discovery
    state['domains'] = {domain:120}
    service, = m.discover_services(root)
    assert service.domain == f'{domain}/{m.os.getuid()}'
    assert (service.pid, service.birth, service.plist_bytes) == (120, 1001, plist.read_bytes())


def test_refuses_duplicate_domain(discovery):
    root, _, _, state = discovery
    state['domains']['user'] = 120
    with pytest.raises(m.LifecycleRefused): m.discover_services(root)


def test_no_pid_is_still_registered(discovery):
    root, _, _, state = discovery
    state['domains']['gui'] = None; state['table'].clear()
    service, = m.discover_services(root)
    assert service.loaded and service.pid is None


def test_unmanaged_gateway_refuses(discovery):
    root, _, _, state = discovery
    state['domains'].clear()
    with pytest.raises(m.LifecycleRefused): m.discover_services(root)


@pytest.mark.parametrize('extra', [('--replace',), ('--unknown-future-flag',)])
def test_unmanaged_gateway_unknown_flags_do_not_bypass_refusal(discovery, extra):
    root, _, _, state = discovery
    state['domains'].clear()
    old = state['table'][120]
    state['table'][120] = replace(old, argv=old.argv + extra)
    with pytest.raises(m.LifecycleRefused, match='Unmanaged'):
        m.discover_services(root)


def test_update_process_is_not_a_gateway(discovery):
    root, _, _, state = discovery
    state['domains'].clear()
    old = state['table'][120]
    state['table'] = {999: replace(old, pid=999,
        argv=(old.argv[0], '-m', 'clover_cli.main', 'update'))}
    assert m.discover_services(root) == []


def test_no_service_builder_refuses_timeout(discovery):
    root, _, _, state = discovery
    state['domains'].clear()
    state['table'] = {300: m.Process(300, 1, 1002, ('node','npm'), root, {})}
    ops = m.LaunchdOperations(roots=(root,))
    children = ops.capture_builders([])
    with pytest.raises(m.LifecycleRefused, match='quiescent'):
        ops.wait_quiescent([], children, 0)


def test_mismatched_home_refuses(discovery):
    root, home, plist, state = discovery
    state['table'][120] = replace(state['table'][120], env={'CLOVER_HOME': str(home / 'other')})
    with pytest.raises(m.LifecycleRefused):
        m.discover_services(root)


def test_inaccessible_registration_refuses(discovery, monkeypatch):
    root, _, _, _ = discovery
    monkeypatch.setattr(m, '_launchctl', lambda *args: m.CommandResult(1, '', 'permission denied'))
    with pytest.raises(m.LifecycleRefused):
        m.discover_services(root)


def test_non_darwin_no_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(m.sys, 'platform', 'linux')
    monkeypatch.setattr(m, 'discover_services', lambda root: pytest.fail('unexpected probe'))
    with m.darwin_workspace_window(tmp_path): pass
