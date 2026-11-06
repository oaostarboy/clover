"""Owned, hosted-only controlled crash for the staged updater fixture."""
from interrupted_identity import Refusal
from pathlib import Path
import sys


def validate_frozen_command(argv, runtime, frozen, beacon, roots):
    """Accept only the pinned candidate's isolated frozen-watcher bootstrap."""
    bootstrap = (
        'import sys,runpy; from pathlib import Path; '
        f'roots={list(map(str, roots))!r}; sys.path[:0]=roots; '
        'import psutil; '
        'assert any(Path(psutil.__file__).resolve().is_relative_to(Path(p)) for p in roots), "untrusted psutil"; '
        'assert psutil.Process().create_time() > 0; '
        'check=sys.argv[-1] == "--check-trusted-watcher"; '
        'sys.argv=sys.argv[1:]; '
        'print("trusted-watcher-ready") if check else runpy.run_path(sys.argv[0], run_name="__main__")'
    )
    expected = [str(runtime), '-I', '-S', '-c', bootstrap, str(frozen), str(beacon)]
    if list(argv) != expected:
        raise Refusal('rollback watcher differs from pinned isolated command')


def require_fixture_port_closed(port):
    """Probe only the fixture's IPv4 loopback endpoint, never host processes."""
    import errno
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        status = probe.connect_ex(('127.0.0.1', port))
    if status != errno.ECONNREFUSED:
        raise Refusal('fixture endpoint is listening or its absence is uncertain')


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
        if owner in ('_cmd_update_impl', '_arm_restart_watcher_before_pause') and beacon_path in argv:
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


def atomic_receipt(path, value):
    import json
    import os
    import tempfile
    fd, name = tempfile.mkstemp(prefix='.fault-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def persist_pins_then_launch(evidence, commit, tree, utility_pins, helper_pins, argv, launch):
    import os
    atomic_receipt(Path(evidence) / 'interrupted-preflight-pins.json',
                   {'schema_version': 1, 'stage': 'pre_launch', 'product_commit': commit,
                    'product_tree': tree, 'source_and_tool_pins': utility_pins,
                    'helper_pins': helper_pins, 'tracee_argv': argv})
    directory = os.open(evidence, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return launch()


class FaultProgress:
    def __init__(self, evidence):
        self.evidence = Path(evidence)
        self.phase = 'not_started'
        self.cleanup = 'not_started'

    def snapshot(self):
        return {'injected': {'not_started': False, 'attempted': None, 'confirmed': True}[self.phase],
                'controlled_injection': self.phase, 'cleanup': self.cleanup}

    def persist(self):
        atomic_receipt(self.evidence / 'interrupted-fault-progress.json', self.snapshot())

    def controlled(self, phase):
        previous = self.phase
        self.phase = phase
        try:
            self.persist()
        except BaseException:
            if phase == 'attempted':
                self.phase = previous  # no signal has been sent
            raise


def write_failure_receipt(evidence, progress, exc):
    atomic_receipt(Path(evidence) / 'interrupted-fault-failure.json',
                   {**progress.snapshot(), 'error': type(exc).__name__, 'detail': str(exc)})


def bind_original_pidfd(pid, birth_ticks, identity_check):
    import os
    try:
        fd = os.pidfd_open(pid, 0)
    except (AttributeError, OSError) as exc:
        raise Refusal('cannot bind original gateway pidfd') from exc
    try:
        if identity_check() != birth_ticks:
            raise Refusal('original gateway identity changed after pidfd binding')
        return fd
    except BaseException:
        os.close(fd)
        raise


def require_original_exit(old_pidfd, old_pid, port):
    import select
    import psutil
    try:
        ready = bool(select.select([old_pidfd], [], [], 0)[0])
    except (OSError, ValueError, TypeError) as exc:
        raise Refusal('original gateway pidfd unavailable') from exc
    if not ready:
        raise Refusal('original gateway pidfd not ready')
    if psutil.pid_exists(old_pid):
        raise Refusal('original gateway PID still exists at crash boundary')
    if port:
        require_fixture_port_closed(port)
    return ready


def execute_fault(stream, updater, old_pid, old_birth, old_argv, home, target, beacon, *, old_pidfd, port, tracked, beacon_copy=None, frozen_check=None, progress=None):
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
            persistence_error = None
            if progress is not None:
                progress.cleanup = 'attempted'
                try:
                    progress.persist()
                except BaseException as exc:
                    persistence_error = exc
            try:
                identity.signal_bound(updater.pid, updater_birth, signal.SIGKILL)
                updater.wait(timeout=10)
            except BaseException:
                if progress is not None:
                    progress.cleanup = 'uncertain'
                    progress.persist()
                raise
            if progress is not None:
                progress.cleanup = 'confirmed' if updater.returncode == -signal.SIGKILL else 'uncertain'
                progress.persist()
            if persistence_error is not None:
                raise persistence_error

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
                ready = require_original_exit(old_pidfd, old_pid, port)
                mark('original_gateway_exit_confirmed', pid=old_pid, birth_ticks=old_birth, pidfd_ready=ready)
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
                if progress is not None:
                    progress.controlled('attempted')
                identity.signal_bound(updater.pid, updater_birth, signal.SIGKILL)
                updater.wait(timeout=10)
                if updater.returncode != -signal.SIGKILL:
                    raise Refusal('updater did not die from SIGKILL')
                if progress is not None:
                    progress.controlled('confirmed')
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
    # Parent-cleared after independent receipt/observer review. Hosted/private
    # preflight above remains mandatory; this is not product release approval.
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
               launch_functions={'caller':['_cmd_update_impl', '_arm_restart_watcher_before_pause'], 'gateway':['_spawn_gateway_restart_watcher']})
    parent, child = socket.socketpair()
    parent.settimeout(1800)
    cfg['fd'] = child.fileno()
    config = runner / 'interrupted-trace-config.json'
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
    progress = FaultProgress(evidence)
    tracee_stderr_fd = None
    old_pidfd = None
    try:
        def recheck_original():
            current = psutil.Process(old_pid)
            ticks = identity.birth(old_pid)
            if (current.cmdline() != old_argv or current.environ().get('CLOVER_HOME') != str(home)
                    or current.exe() != str((target / 'venv/bin/python').resolve())
                    or current.uids().real != os.getuid() or current.status() == psutil.STATUS_ZOMBIE):
                raise Refusal('original gateway identity changed after pidfd binding')
            return ticks
        old_pidfd = bind_original_pidfd(old_pid, old_birth, recheck_original)
        tracee_stderr_fd = os.open(evidence / 'interrupted-tracee-stderr.log',
                                   os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        cfg['stderr_fd'] = tracee_stderr_fd
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(cfg, out)
        progress.persist()
        def inject(updater):
            child.close()
            stream = parent.makefile('rwb', buffering=0)
            try:
                stream.write(f'bind {updater.pid}\n'.encode())
                def check_frozen(bindings):
                    rollback = bindings['rollback']['pid']
                    watcher = psutil.Process(rollback)
                    validate_frozen_command(watcher.cmdline(), runtime, frozen,
                                            home / '.clover-update-heartbeat.json', [args.dependencies])
                    if watcher.environ().get('PATH') != env.get('PATH'):
                        raise Refusal('rollback watcher PATH mismatch')
                    beacon_data = json.loads((home / '.clover-update-heartbeat.json').read_text())
                    if beacon_data.get('venv_python') not in (None, str(python_probe)):
                        raise Refusal('unapproved import probe Python in watcher beacon')
                    check_helpers(helper_pins)
                    bound_rules.extend(bind_rules(utility_pins, frozen, rollback_pid=rollback))
                result = execute_fault(stream, updater, old_pid, old_birth, old_argv, home, target,
                                       home / '.clover-update-heartbeat.json', old_pidfd=old_pidfd, port=18791, tracked=[],
                                       beacon_copy=evidence / 'interrupted-bound-beacon.json',
                                       frozen_check=check_frozen, progress=progress)
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

        result = persist_pins_then_launch(
            evidence, args.commit, args.tree, utility_pins, helper_pins, argv,
            lambda: run_observed_recovery(binary, argv, env.copy(), home, stage,
                                          evidence / 'interrupted-process-trace',
                                          (child.fileno(), tracee_stderr_fd), inject, recover))
        (evidence / 'interrupted-observed.json').write_text(json.dumps(result, indent=2) + '\n')
    except BaseException as exc:
        try:
            write_failure_receipt(evidence, progress, exc)
        except BaseException as receipt_error:
            raise RuntimeError(f'unable to persist failure receipt; progress={progress.snapshot()!r}; '
                               f'original error={type(exc).__name__}: {exc}') from receipt_error
        raise
    finally:
        if old_pidfd is not None:
            os.close(old_pidfd)
        if tracee_stderr_fd is not None:
            os.close(tracee_stderr_fd)
        child.close()
        parent.close()


if __name__ == '__main__':
    main()
