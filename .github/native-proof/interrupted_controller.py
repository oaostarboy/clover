"""Owned, hosted-only controlled crash for the staged updater fixture."""
from interrupted_identity import Refusal
from pathlib import Path
import sys


def hosted_preflight(env, runner_temp, home, install, evidence):
    if (sys.platform != 'linux' or env.get('GITHUB_ACTIONS') != 'true'
            or env.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or env.get('NATIVE_INTERRUPTED_RECOVERY') != '1'):
        raise Refusal('requires explicit hosted Linux fault mode')
    paths = [Path(p) for p in (runner_temp, home, install, evidence)]
    if any(not p.is_absolute() or p.resolve() != p for p in paths):
        raise Refusal('noncanonical fixture path')
    runner, home, install, evidence = paths
    if (env.get('CLOVER_HOME') != str(home) or env.get('CLOVER_INSTALL_DIR') != str(install)
            or env.get('RUNNER_TEMP') != str(runner) or install != home / 'clover-c1'
            or not evidence.is_relative_to(runner) or home == runner or runner in home.parents
            or not home.is_relative_to(Path(env.get('HOME', '/')).resolve())):
        raise Refusal('fixture path binding mismatch')


def select_watchers(events, updater_pid, old_pid, beacon_path):
    selected = {}
    for event in events:
        if event.get('stage') != 'launch':
            continue
        if event.get('pid') != updater_pid:
            raise Refusal('launch event is not from bound updater')
        argv = event.get('argv')
        if not isinstance(argv, (list, tuple)):
            raise Refusal('invalid launch arguments')
        owner = event.get('owner')
        role = None
        if owner == '_cmd_update_impl' and beacon_path in argv:
            role = 'rollback'
        elif (owner == '_spawn_gateway_restart_watcher' and len(argv) >= 4
              and argv[1] == '-c' and argv[3] == str(old_pid)):
            role = 'restart'
        if role:
            pid = event.get('child_pid')
            if not isinstance(pid, int) or pid <= 1 or role in selected:
                raise Refusal('invalid or duplicate watcher')
            selected[role] = event
    if set(selected) != {'rollback', 'restart'}:
        raise Refusal('missing watcher role')
    if selected['rollback']['child_pid'] == selected['restart']['child_pid']:
        raise Refusal('watcher roles are not distinct')
    return selected


def execute_fault(stream, updater, old_pid, old_birth, old_argv, home, target, beacon, *, port, tracked, beacon_copy=None):
    """Perform the controlled crash; a return means injection, not recovery."""
    import hashlib
    import json
    import os
    import signal
    import time
    import psutil
    import interrupted_identity as identity
    import interrupted_ownership as ownership
    from interrupted_schedule import frozen_watchers

    events, launches, bindings = [], [], {}
    updater_birth = identity.birth(updater.pid)
    updater_created = psutil.Process(updater.pid).create_time()

    def mark(stage, **kwargs):
        events.append({'stage': stage, 'monotonic_ns': time.monotonic_ns(),
                       'wall_ns': time.time_ns(), **kwargs})

    def read_event(expected):
        while True:
            raw = stream.readline()
            if not raw:
                raise Refusal('trace closed before ' + expected)
            event = json.loads(raw)
            if event.get('pid') != updater.pid:
                raise Refusal('trace updater PID mismatch')
            if event.get('stage') == 'launch':
                launches.append(event)
                continue
            if event.get('stage') != expected:
                raise Refusal('unexpected trace stage')
            mark(expected, frame=event.get('frame'), caller=event.get('caller'))
            return event

    def kill_updater():
        if updater.poll() is None:
            identity.signal_bound(updater.pid, updater_birth, signal.SIGKILL)
            updater.wait(timeout=10)

    def bind(role, event):
        argv = event['argv']
        physical = str(Path(argv[0]).resolve(strict=True))
        proc = psutil.Process(event['child_pid'])
        binding = ownership.bind_child(proc, updater_pid=updater.pid, expected_argv=argv,
                                        expected_exe=physical, home=home, cwd=target,
                                        uid=os.getuid())
        return proc, binding

    try:
        read_event('prepare')
        selected = select_watchers(launches, updater.pid, old_pid, str(beacon))
        for role, event in selected.items():
            proc, data = bind(role, event)
            bindings[role] = (proc, data)
            tracked.append(proc.pid)
        ownership.validate_beacon(beacon, home=home, repo=target, updater_pid=updater.pid,
                                  updater_create_time=updater_created, gateway_argv=old_argv)
        for role, event in selected.items():
            _, current = bind(role, event)
            if current != bindings[role][1]:
                raise Refusal('watcher binding changed before stop')
        with frozen_watchers([(proc, data['birth_ticks']) for proc, data in bindings.values()]):
            mark('watchers_stopped', pids=[p.pid for p, _ in bindings.values()])
            try:
                stream.write(b'continue\n')
                read_event('wait')
                # Zombies do not establish that the original is gone.
                if psutil.pid_exists(old_pid):
                    raise Refusal('original gateway PID still exists at crash boundary')
                if port:
                    listeners = [c for c in psutil.net_connections(kind='inet')
                                 if c.status == psutil.CONN_LISTEN and c.laddr.port == port]
                    if listeners:
                        raise Refusal('listener exists at crash boundary')
                state_path = Path(home) / 'gateway_state.json'
                if state_path.exists():
                    state = json.loads(state_path.read_text())
                    if state.get('pid') not in (None, old_pid):
                        raise Refusal('replacement gateway state exists at crash boundary')
                for role, event in selected.items():
                    _, current = bind(role, event)
                    if current != bindings[role][1] or identity.state(event['child_pid']) not in ('T', 't'):
                        raise Refusal('watcher binding or stopped state changed')
                beacon_binding = ownership.validate_beacon(beacon, home=home, repo=target,
                    updater_pid=updater.pid, updater_create_time=updater_created, gateway_argv=old_argv)
                if beacon_copy is not None:
                    raw = Path(beacon).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != beacon_binding['sha256']:
                        raise Refusal('beacon changed before preservation')
                    Path(beacon_copy).write_bytes(raw)
                kill_updater()
                if updater.returncode != -signal.SIGKILL:
                    raise Refusal('updater did not die from SIGKILL')
                mark('updater_killed', pid=updater.pid, returncode=updater.returncode)
            except BaseException:
                kill_updater()
                raise
        mark('watchers_resumed')
        return {'injected': True, 'events': events, 'beacon': beacon_binding,
                'watchers': {role: data for role, (_, data) in bindings.items()},
                'launches': [{'owner': e['owner'], 'owner_file': e['owner_file'],
                              'child_pid': e['child_pid'],
                              'argv_sha256': hashlib.sha256(json.dumps(e['argv']).encode()).hexdigest()}
                             for e in selected.values()],
                'original_pid': old_pid, 'original_birth': old_birth}
    finally:
        kill_updater()


def main():
    import argparse
    import json
    import os
    import socket
    import subprocess
    import psutil
    import interrupted_identity as identity
    from interrupted_source import pinned_hash

    parser = argparse.ArgumentParser()
    for option in ('stage', 'runtime', 'home', 'target', 'evidence', 'old', 'commit', 'tree', 'dependencies'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args()
    env = os.environ
    runner = Path(env['RUNNER_TEMP'])
    home, target, evidence, stage, runtime = map(Path, (args.home, args.target, args.evidence, args.stage, args.runtime))
    hosted_preflight(env, str(runner), str(home), str(target), str(evidence))
    if (stage != runner / 'trusted-stage' or runtime != runner / 'trusted-runtime' / 'bin' / 'python'
            or stage.resolve() != stage or not Path(args.dependencies).is_absolute()):
        raise Refusal('untrusted stage or runtime path')
    old_state = json.loads((evidence / 'before-state.json').read_text())
    old_pid = old_state['pid']
    if old_pid <= 1:
        raise Refusal('invalid original gateway PID')
    old = psutil.Process(old_pid)
    old_birth = identity.birth(old_pid)
    old_argv = old.cmdline()
    if (str(old_birth) != str(old_state['start_time']) or old.environ().get('CLOVER_HOME') != str(home)
            or old.exe() != str((target / 'venv/bin/python').resolve())
            or old.uids().real != os.getuid() or old.status() == psutil.STATUS_ZOMBIE
            or not old_argv or not any(v in old_argv for v in ('run', 'restart'))):
        raise Refusal('original gateway identity mismatch')
    files = {'entry':'scripts/staged_update.py', 'caller':'clover_cli/update_cmd.py',
             'gateway':'clover_cli/gateway.py'}
    cfg = {name: str(stage / rel) for name, rel in files.items()}
    cfg.update({name + '_hash': pinned_hash(stage, args.commit, args.tree, rel)
                for name, rel in files.items()})
    cfg.update(caller_alias=str(target / files['caller']), gateway_alias=str(target / files['gateway']),
               home=str(home), prepare='_prepare_profile_gateway_update_restart', wait='_wait_for_gateway_exit',
               caller_function='_cmd_update_impl',
               launch_functions={'caller':['_cmd_update_impl'], 'gateway':['_spawn_gateway_restart_watcher']})
    parent, child = socket.socketpair()
    parent.settimeout(1800)
    cfg['fd'] = child.fileno()
    config = runner / 'interrupted-trace-config.json'
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(cfg, out)
    argv = [str(runtime), '-I', '-S', str(Path(__file__).with_name('interrupted_trace.py').resolve()),
            str(config), '--', '--target', str(target), '--old-commit', args.old,
            '--stage-commit', args.commit, '--stage-tree', args.tree, '--home', str(home),
            '--dependency-path', args.dependencies]
    updater = None
    updater_birth = None
    try:
        updater = subprocess.Popen(argv, env=env.copy(), cwd=str(stage), pass_fds=(child.fileno(),))
        child.close()
        proc = psutil.Process(updater.pid)
        updater_birth = identity.birth(updater.pid)
        if (updater.pid <= 1 or proc.ppid() != os.getpid() or proc.exe() != str(runtime.resolve())
                or proc.environ().get('CLOVER_HOME') != str(home) or proc.uids().real != os.getuid()
                or identity.birth(updater.pid) != updater_birth):
            raise Refusal('updater identity mismatch')
        stream = parent.makefile('rwb', buffering=0)
        try:
            stream.write(f'bind {updater.pid}\n'.encode())
            result = execute_fault(stream, updater, old_pid, old_birth, old_argv, home, target,
                                   home / '.clover-update-heartbeat.json', port=18791, tracked=[],
                                   beacon_copy=evidence / 'interrupted-bound-beacon.json')
        finally:
            stream.close()
        result['updater'] = {'pid': updater.pid, 'birth_ticks': updater_birth,
                             'executable': str(runtime.resolve())}
        (evidence / 'interrupted-fault.json').write_text(json.dumps(result, indent=2) + '\n')
    except BaseException as exc:
        (evidence / 'interrupted-fault-failure.json').write_text(json.dumps(
            {'injected': False, 'error': type(exc).__name__, 'detail': str(exc)}, indent=2) + '\n')
        raise
    finally:
        child.close()
        parent.close()
        if updater is not None and updater.poll() is None and updater_birth is not None:
            identity.signal_bound(updater.pid, updater_birth, __import__('signal').SIGKILL)
            updater.wait(timeout=10)


if __name__ == '__main__':
    main()
