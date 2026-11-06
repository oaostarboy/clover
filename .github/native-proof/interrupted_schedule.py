"""Prototype scheduling helpers; not an authorization or production controller.

Callers must establish watcher roles, ancestry, private home, executable and
argv before using these helpers. This module is not wired into the native test.
"""
from contextlib import contextmanager
import os
import signal
import time

import interrupted_identity as identity

Refusal = identity.Refusal


@contextmanager
def frozen_watchers(watchers):
    if len(watchers) != 2 or len({p.pid for p, _ in watchers}) != 2:
        raise Refusal('exactly two distinct bound watchers required')
    stopped = []
    try:
        for proc, birth in watchers:
            identity.signal_bound(proc.pid, birth, signal.SIGSTOP)
            stopped.append((proc, birth))
            end = time.monotonic() + 3
            while identity.state(proc.pid) not in ('T', 't'):
                if time.monotonic() >= end:
                    raise Refusal('watcher failed to stop')
                time.sleep(.01)
        yield
    finally:
        failures = []
        for proc, birth in stopped:
            try:
                identity.signal_bound(proc.pid, birth, signal.SIGCONT)
            except (Refusal, ProcessLookupError) as exc:
                failures.append(str(exc))
        if failures:
            raise Refusal('resume failed: ' + '; '.join(failures))


def check_boundary(old, replacements):
    proc, birth = old
    try:
        fd = os.pidfd_open(proc.pid)
    except ProcessLookupError:
        fd = None
    except OSError as exc:
        raise Refusal('cannot determine original gateway identity') from exc
    if fd is not None:
        try:
            actual = identity.birth(proc.pid)
            if actual == birth:
                raise Refusal('original gateway is still alive')
        finally:
            os.close(fd)
    if replacements:
        raise Refusal('replacement already exists')
