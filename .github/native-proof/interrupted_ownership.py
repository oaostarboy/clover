"""Identity binding for private Linux recovery-test children.

Not a discovery policy: the controller must obtain expected argv from the
pinned product launch, and stop only after its hosted/private-root preflight.
Re-bind immediately before each signal; pidfd birth checking is also required.
"""
import hashlib
import json
from pathlib import Path
import stat

import interrupted_identity as identity
Refusal = identity.Refusal


def bind_child(proc, *, updater_pid, expected_argv, expected_exe, home, cwd, uid):
    if proc.pid <= 1 or updater_pid <= 1 or proc.pid == updater_pid or not expected_argv:
        raise Refusal('invalid private child identity')
    def snapshot():
        uids = proc.uids()
        return (identity.birth(proc.pid), proc.create_time(), proc.ppid(),
                proc.exe(), tuple(proc.cmdline()), proc.cwd(),
                proc.environ().get('CLOVER_HOME'), uids.real, uids.effective)
    before = snapshot()
    after = snapshot()
    if before != after:
        raise Refusal('child identity changed during inspection')
    ticks, created, parent, exe, argv, actual_cwd, actual_home, real, effective = before
    if (parent != updater_pid or exe != expected_exe or argv != tuple(expected_argv)
            or actual_cwd != str(cwd) or actual_home != str(home)
            or real != uid or effective != uid or not created):
        raise Refusal('child is not the exact private watcher')
    return {'pid': proc.pid, 'birth_ticks': ticks, 'create_time': created,
            'parent_pid': parent, 'executable': exe, 'cwd': actual_cwd,
            'home': actual_home, 'uid': uid,
            'argv_sha256': hashlib.sha256(json.dumps(argv).encode()).hexdigest()}


def validate_beacon(path, *, home, repo, updater_pid, updater_create_time, gateway_argv):
    path, home, repo = Path(path), Path(home), Path(repo)
    if path.is_symlink() or not path.resolve().is_relative_to(home.resolve()):
        raise Refusal('beacon outside private home or symlinked')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise Refusal('beacon is not a regular file')
    raw = path.read_bytes()
    after = path.stat()
    if ((before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size)
            != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size)
            or raw != path.read_bytes()):
        raise Refusal('beacon changed during inspection')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise Refusal('invalid beacon') from exc
    if not isinstance(data, dict) or any(data.get(k) != v for k, v in {
        'updater_pid': updater_pid, 'updater_create_time': updater_create_time,
        'cwd': str(repo), 'repo': str(repo), 'gateway_argv': list(gateway_argv),
    }.items()):
        raise Refusal('beacon does not bind this updater and installation')
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'inode': before.st_ino,
            'updater_pid': updater_pid, 'updater_create_time': updater_create_time}
