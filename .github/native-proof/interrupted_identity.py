"""Linux PID/birth guard for the interrupted-update test fixture.

This primitive alone does not establish private-process ownership; callers must
first bind the expected executable, argv, home, ancestry, and watcher role.
"""
import os
import signal


class Refusal(RuntimeError):
    pass


def birth(pid):
    try:
        with open(f'/proc/{pid}/stat', encoding='ascii') as stat:
            data = stat.read()
        # The parenthesized comm may itself contain whitespace and parentheses.
        return int(data[data.rfind(')') + 2:].split()[19])
    except (OSError, ValueError, IndexError) as exc:
        raise Refusal(f'cannot establish birth for PID {pid}') from exc


def state(pid):
    try:
        with open(f'/proc/{pid}/stat', encoding='ascii') as stat:
            data = stat.read()
        return data[data.rfind(')') + 2:].split()[0]
    except (OSError, IndexError) as exc:
        raise Refusal(f'cannot establish state for PID {pid}') from exc


def signal_bound(pid, expected_birth, sig):
    if pid <= 1 or not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise Refusal('pidfd is required')
    try:
        fd = os.pidfd_open(pid)
    except OSError as exc:
        raise Refusal('cannot open pidfd') from exc
    try:
        if birth(pid) != expected_birth:
            raise Refusal('PID birth mismatch')
        signal.pidfd_send_signal(fd, sig)
    finally:
        os.close(fd)
