#!/usr/bin/env python3
"""One-shot hosted-macOS ordinary-user launchd isolation diagnostic."""
import hashlib, json, os, pathlib, plistlib, pwd, re, subprocess, sys, time, traceback

E = pathlib.Path(os.environ.get('RUNNER_TEMP', '/nonexistent')) / 'evidence'
state = {'phase': 'init', 'result': 'FAIL', 'cleanup': 'not-started'}

def save(phase, **kwargs):
    state.update(phase=phase, **kwargs)
    E.mkdir(parents=True, exist_ok=True)
    (E / 'result.json').write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')

def run(args, check=True, timeout=20):
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError('%s: exit %d: %s' % (args[0], p.returncode, p.stderr.strip()[:1500]))
    return p

def native_guard():
    if sys.platform != 'darwin' or os.getenv('GITHUB_ACTIONS') != 'true' or os.getenv('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('requires GitHub-hosted macOS')
    temp = pathlib.Path(os.environ.get('RUNNER_TEMP', ''))
    if not temp.is_absolute() or not temp.is_dir() or temp.resolve() != temp:
        raise RuntimeError('unsafe RUNNER_TEMP')
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    if not re.fullmatch(r'[0-9]{1,18}', run_id):
        raise RuntimeError('unsafe run ID')
    return run_id

def verify_dscl_operations():
    result = run(['/usr/bin/dscl', '.', '-help'], check=False)
    text = result.stdout + result.stderr
    if result.returncode or not all(re.search(r'(?m)^\s*-' + op + r'\s', text) for op in ('create', 'read', 'delete')):
        raise RuntimeError('native dscl help does not confirm required operations')


def ordinary_command(uid, user):
    if not isinstance(uid, int) or not 600 <= uid < 1000 or not re.fullmatch(r'cpv[0-9]{1,12}', user):
        raise ValueError('unsafe ordinary-user identity')
    # asuser selects the bootstrap namespace; sudo then drops real/effective UID.
    return ['/usr/bin/sudo', '-n', '/bin/launchctl', 'asuser', str(uid), '/usr/bin/sudo', '-n', '-H', '-u', user]

def main():
    run_id = native_guard()
    suffix = run_id[-12:]
    user = 'cpv' + suffix
    home = '/Users/' + user
    label = 'io.clover.ci.probe.' + run_id
    if not re.fullmatch(r'cpv[0-9]{1,12}', user) or not re.fullmatch(r'io\.clover\.ci\.probe\.[0-9]{1,18}', label):
        raise RuntimeError('unsafe identity')
    save('preflight', username=user, home=home, label=label, script_sha256=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest())
    # A negative lookup must unambiguously be the native dscl record-not-found error.
    check = run(['/usr/bin/dscl', '.', '-read', '/Users/' + user], check=False)
    if check.returncode == 0 or set(re.findall(r'\beDS[A-Za-z0-9_]+\b', check.stdout + check.stderr)) != {'eDSRecordNotFound'}:
        raise RuntimeError('account already exists or account lookup inconclusive: ' + check.stderr.strip()[:500])
    if pathlib.Path(home).exists() or pathlib.Path(home).is_symlink():
        raise RuntimeError('home collision')
    verify_dscl_operations()
    save('dscl-help-verified')
    entries = run(['/usr/bin/dscl', '.', '-list', '/Users', 'UniqueID']).stdout
    used = {int(line.split()[-1]) for line in entries.splitlines() if line.split() and line.split()[-1].isdigit()}
    uid = next((n for n in range(600, 1000) if n not in used), None)
    if uid is None: raise RuntimeError('no unused UID in bounded range')
    domain = 'user/' + str(uid)
    record = '/Users/' + user
    created = False
    creation_attempted = False
    bootstrap_attempted = False
    failed = None
    save('account-provisioning', uid=uid, domain=domain, runner_uid=os.getuid(), runner_home=pwd.getpwuid(os.getuid()).pw_dir)
    try:
        # Only this newly checked name may be created; each step is bounded.
        creation_attempted = True
        run(['/usr/bin/sudo', '-n', '/usr/bin/dscl', '.', '-create', record])
        created = True
        for key, value in [('UniqueID', str(uid)), ('PrimaryGroupID', '20'), ('NFSHomeDirectory', home), ('UserShell', '/usr/bin/false'), ('RealName', 'Disposable CI probe')]:
            run(['/usr/bin/sudo', '-n', '/usr/bin/dscl', '.', '-create', record, key, value])
        run(['/usr/bin/sudo', '-n', '/bin/mkdir', '-m', '700', home])
        run(['/usr/bin/sudo', '-n', '/usr/sbin/chown', str(uid) + ':20', home])
        save('account-created')
        identity_code = 'import os,pwd,json; p=pwd.getpwuid(os.getuid()); print(json.dumps([os.getuid(),os.geteuid(),p.pw_name,p.pw_dir,os.environ.get("HOME")]))'
        as_user = ordinary_command(uid, user)
        identity = json.loads(run(as_user + ['/usr/bin/python3', '-c', identity_code], timeout=60).stdout)
        if identity != [uid, uid, user, home, home] or uid == os.getuid() or home == pwd.getpwuid(os.getuid()).pw_dir:
            raise RuntimeError('ordinary-user identity mismatch: ' + repr(identity))
        groups = run(as_user + ['/usr/bin/id', '-Gn']).stdout.split()
        if 'admin' in groups or 'wheel' in groups:
            raise RuntimeError('fixture unexpectedly has administrative group membership')
        save('identity-verified', identity=identity)
        manager = run(as_user + ['/bin/launchctl', 'managername'], check=False)
        dom = run(as_user + ['/bin/launchctl', 'print', domain], check=False)
        save('domain-inspected', manager_status=manager.returncode, manager_name=manager.stdout.strip(), domain_status=dom.returncode, domain_error=dom.stderr.strip()[:1500])
        if manager.returncode or dom.returncode:
            raise RuntimeError('ordinary user launchd domain unavailable: ' + dom.stderr.strip()[:1500])
        # Generate plist as the unprivileged owner; never create a product service.
        directory = home + '/Library/LaunchAgents'
        run(as_user + ['/bin/mkdir', '-p', directory])
        plist = directory + '/' + label + '.plist'
        data = plistlib.dumps({'Label': label, 'ProgramArguments': ['/bin/sleep', '120'], 'RunAtLoad': True})
        p = subprocess.run(as_user + ['/usr/bin/tee', plist], input=data, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
        if p.returncode: raise RuntimeError('plist write failed')
        save('before-bootstrap')
        bootstrap_attempted = True
        run(as_user + ['/bin/launchctl', 'bootstrap', domain, plist])
        result = run(as_user + ['/bin/launchctl', 'print', domain + '/' + label]).stdout
        match = re.search(r'\bpid\s*=\s*(\d+)', result)
        if not match: raise RuntimeError('launchd service has no PID')
        pid = int(match.group(1))
        ps = run(as_user + ['/bin/ps', '-p', str(pid), '-o', 'uid=,lstart=,command=']).stdout.strip()
        if not re.match(r'^\s*' + str(uid) + r'\s+', ps) or not re.search(r'/bin/sleep\s+120\s*$', ps):
            raise RuntimeError('service process identity/argv mismatch')
        # Same-user observer reads the real service's process info, not a root observer.
        save('service-verified', service_pid=pid, same_uid_process_visibility=True, birth_observed=bool(ps), argv_verified=True, runner_and_fixture_domains_distinct=(uid != os.getuid()), home_distinct=True)
        state['result'] = 'PASS'
    except Exception as exc:
        failed = exc
        save('failed', error=str(exc)[:2000])
    finally:
        save('cleanup-started')
        cleanup_errors = []
        if creation_attempted and not created:
            cleanup_errors.append('account creation outcome uncertain; left untouched')
        if created:
            # Verify UID and home association before touching either object.
            record_check = run(['/usr/bin/dscl', '.', '-read', record, 'UniqueID', 'NFSHomeDirectory'], check=False)
            text = record_check.stdout
            if record_check.returncode or not re.search(r'(?m)^UniqueID:\s*' + str(uid) + r'\s*$', text) or not re.search(r'(?m)^NFSHomeDirectory:\s*' + re.escape(home) + r'\s*$', text):
                cleanup_errors.append('identity binding uncertain; left account untouched')
            else:
                if bootstrap_attempted:
                    p = run(ordinary_command(uid, user) + ['/bin/launchctl', 'bootout', domain + '/' + label], check=False)
                    if p.returncode: cleanup_errors.append('service bootout failed: ' + p.stderr.strip()[:500])
                if not cleanup_errors:
                    p = run(['/usr/bin/sudo', '-n', '/usr/bin/dscl', '.', '-delete', record], check=False)
                    if p.returncode: cleanup_errors.append('account delete failed: ' + p.stderr.strip()[:500])
                    if not cleanup_errors and pathlib.Path(home).exists():
                        if pathlib.Path(home).is_symlink() or pathlib.Path(home).stat().st_uid != uid:
                            cleanup_errors.append('home ownership uncertain')
                        else:
                            p = run(['/usr/bin/sudo', '-n', '/bin/rm', '-rf', '--', home], check=False)
                            if p.returncode: cleanup_errors.append('home removal failed')
                    verify = run(['/usr/bin/dscl', '.', '-read', record], check=False)
                    if verify.returncode == 0 or 'eDSRecordNotFound' not in verify.stderr or pathlib.Path(home).exists(): cleanup_errors.append('account/home removal not verified')
        if cleanup_errors: state['result'] = 'FAIL'
        save('complete', cleanup='FAIL' if cleanup_errors else 'PASS', cleanup_errors=cleanup_errors)
    if failed or cleanup_errors: raise RuntimeError('diagnostic or cleanup failed')

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        if E.is_dir() and state.get('phase') != 'complete':
            save('aborted', result='FAIL', error=str(exc)[:2000])
        print('FAIL:', str(exc), file=sys.stderr)
        sys.exit(1)
