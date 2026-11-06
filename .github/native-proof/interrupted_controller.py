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


def execute_fault(stream, updater, old_pid, old_birth, old_argv, home, target, beacon, *, port, tracked, beacon_copy=None, frozen_check=None):
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
            if frozen_check is not None:
                frozen_check({role: data for role, (_, data) in bindings.items()})
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


def run_observed_recovery(binary, argv, env, home, cwd, prefix, pass_fds,
                          inject, recover):
    """Keep the owned tracer alive until recovery assertions have finished."""
    import time
    from interrupted_traced_updater import TraceSession
    with TraceSession(binary, argv, env, home, cwd, prefix, pass_fds) as session:
        start = time.time_ns()
        result = inject(session.updater)
        if session.tracer.poll() is not None:
            raise Refusal('tracer exited before recovery observation')
        recovery = recover(session, result)
        if session.tracer.poll() is not None:
            raise Refusal('tracer exited during recovery observation')
        result['recovery'] = recovery
        result['trace'] = {'pid': session.tracer.pid,
                           'birth_ticks': session.tracer_start_ticks,
                           'prefix': str(prefix), 'start_wall_ns': start,
                           'end_wall_ns': time.time_ns()}
    return result


def finish_accounting(session, fault, home, target, after, rules, destination, *, timeout=15):
    from interrupted_observation import bind_successor, account
    successor = bind_successor(after, home, target)
    result = account(session, fault, successor['pid'], successor['argv'], rules, destination, timeout=timeout)
    if result['gateway_birth_ticks'] != successor['birth_ticks']:
        raise Refusal('successor birth changed before observation')
    result['successor'] = successor
    return result


def verify_recovery(python, env, home, target, stage, evidence, old, commit):
    """Finish every recovery and chat assertion before tracer cleanup."""
    import subprocess
    import time
    fixture = Path(__file__).resolve().parent
    def run(name, *args, timeout=180):
        subprocess.run([str(python), str(fixture / name), *map(str, args)],
                       env=dict(env), check=True, timeout=timeout)
    after = evidence / 'interrupted-after-state.json'
    run('interrupted_recovery.py', home, target, evidence, old, commit, timeout=180)
    run('stage_independence_verify.py', 'identity', home, target, stage, evidence, after)
    (evidence / 'chat-post').touch()
    deadline = time.monotonic() + 120
    reply = evidence / 'chat-post-reply.json'
    while not reply.exists() or reply.stat().st_size == 0:
        if time.monotonic() >= deadline:
            raise Refusal('chat reply timeout')
        time.sleep(0.1)
    run('stage_independence_verify.py', 'session', home, target, stage, evidence, after)
    run('interrupted_chat_verify.py', home, target, evidence)
    (evidence / 'chat-stop').touch()
    return {'verified': True, 'identity': True, 'session': True, 'chat': True}


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
    for option in ('stage', 'runtime', 'home', 'target', 'evidence', 'old', 'commit', 'tree', 'dependencies', 'python', 'recovery-commit'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args()
    env = os.environ
    runner = Path(env['RUNNER_TEMP'])
    home, target, evidence, stage, runtime = map(Path, (args.home, args.target, args.evidence, args.stage, args.runtime))
    hosted_preflight(env, str(runner), str(home), str(target), str(evidence))
    # Atomic gate: do not run a hosted fault until complete launch accounting is
    # implemented and verified. Never silently turn trace collection into proof.
    raise Refusal('hosted recovery held: launch accounting not yet verified')
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
    import shutil
    binary = shutil.which('strace')
    if not binary:
        raise Refusal('strace is required for observed recovery')
    from interrupted_utility_binding import preflight, bind_rules, pin_helpers, check_helpers
    python_probe = target / 'venv/bin/python'
    utility_pins = preflight(stage, args.commit, args.tree, python_probe, env)
    helper_dir = Path(__file__).resolve().parent
    helper_names = ('interrupted_controller.py', 'interrupted_identity.py', 'interrupted_ownership.py',
                    'interrupted_schedule.py', 'interrupted_source.py', 'interrupted_trace.py',
                    'interrupted_traced_updater.py', 'interrupted_launch_accounting.py',
                    'interrupted_utility_binding.py', 'interrupted_observation.py',
                    'interrupted_recovery.py', 'interrupted_chat_verify.py', 'stage_independence_verify.py')
    helper_pins = pin_helpers([helper_dir / n for n in helper_names], binary)
    frozen = home / 'logs/update_restart_watcher.py'
    bound_rules = []
    try:
        def inject(updater):
            child.close()
            stream = parent.makefile('rwb', buffering=0)
            try:
                stream.write(f'bind {updater.pid}\n'.encode())
                def check_frozen(bindings):
                    rollback = bindings['rollback']['pid']
                    watcher = psutil.Process(rollback)
                    if (watcher.cmdline() != [str(runtime), str(frozen), str(home / '.clover-update-heartbeat.json')]
                            or watcher.environ().get('PATH') != env.get('PATH')):
                        raise Refusal('rollback watcher executable, source, argv or PATH mismatch')
                    beacon_data = json.loads((home / '.clover-update-heartbeat.json').read_text())
                    if beacon_data.get('venv_python') not in (None, str(python_probe)):
                        raise Refusal('unapproved import probe Python in watcher beacon')
                    check_helpers(helper_pins)
                    bound_rules.extend(bind_rules(utility_pins, frozen, rollback_pid=rollback))
                result = execute_fault(stream, updater, old_pid, old_birth, old_argv, home, target,
                                       home / '.clover-update-heartbeat.json', port=18791, tracked=[],
                                       beacon_copy=evidence / 'interrupted-bound-beacon.json',
                                       frozen_check=check_frozen)
            finally:
                stream.close()
            result['updater'] = {'pid': updater.pid, 'birth_ticks': updater.birth_ticks,
                                 'executable': str(runtime.resolve())}
            (evidence / 'interrupted-fault.json').write_text(json.dumps(result, indent=2) + '\n')
            return result

        def recover(session, result):
            checks = verify_recovery(args.python, env, home, target, stage, evidence, args.old, args.recovery_commit)
            check_helpers(helper_pins)
            current_rules = bind_rules(utility_pins, frozen, rollback_pid=result['watchers']['rollback']['pid'])
            if current_rules != bound_rules:
                raise Refusal('watcher utility policy changed during recovery')
            accounting = finish_accounting(session, result, home, target,
                                           evidence / 'interrupted-after-state.json', current_rules,
                                           evidence / 'interrupted-trace-snapshot', timeout=30)
            check_helpers(helper_pins)
            if bind_rules(utility_pins, frozen, rollback_pid=result['watchers']['rollback']['pid']) != current_rules:
                raise Refusal('watcher policy changed during accounting')
            return {**checks, **accounting, 'utility_rules': current_rules,
                    'source_and_tool_pins': utility_pins, 'helper_pins': helper_pins}

        result = run_observed_recovery(binary, argv, env.copy(), home, stage,
                                       evidence / 'interrupted-process-trace', (child.fileno(),),
                                       inject, recover)
        (evidence / 'interrupted-observed.json').write_text(json.dumps(result, indent=2) + '\n')
    except BaseException as exc:
        (evidence / 'interrupted-fault-failure.json').write_text(json.dumps(
            {'injected': False, 'error': type(exc).__name__, 'detail': str(exc)}, indent=2) + '\n')
        raise
    finally:
        child.close()
        parent.close()


if __name__ == '__main__':
    main()
