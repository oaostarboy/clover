"""Bind a traced updater without confusing it with the owned tracer.

The tracee is not our waitpid child. Its pidfd proves exit, while strace's
kernel terminal record provides status. Neither a PID file nor a trace line
alone authorizes a signal. This module never signals a process.
"""
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import time

import psutil
import interrupted_identity as identity
from interrupted_identity import Refusal


class BoundTracee:
    def __init__(self, pid, birth_ticks, pidfd, trace_path):
        self.pid = pid
        self.birth_ticks = birth_ticks
        self.pidfd = pidfd
        self.trace_path = Path(trace_path)
        self.returncode = None

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if self.pidfd is None:
            raise Refusal('tracee handle already closed')
        if not select.select([self.pidfd], [], [], 0)[0]:
            return None
        try:
            with self.trace_path.open('rb') as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 4096))
                lines = f.read().decode('ascii', errors='strict').splitlines()
        except FileNotFoundError:
            return None
        terminal = lines[-1] if lines else ''
        match = re.fullmatch(r'(?:\d+\.\d+\s+)?\+\+\+ exited with (\d+) \+\+\+', terminal)
        if match:
            self.returncode = int(match[1])
        else:
            match = re.fullmatch(r'(?:\d+\.\d+\s+)?\+\+\+ killed by (SIG[A-Z0-9]+)(?: \(core dumped\))? \+\+\+', terminal)
            if match:
                value = getattr(signal, match[1], None)
                if not isinstance(value, int):
                    raise Refusal('unknown terminal signal')
                self.returncode = -int(value)
        return self.returncode

    def wait(self, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            status = self.poll()
            if status is not None:
                return status
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('bound tracee exit evidence', timeout)
            time.sleep(0.01)

    def close(self):
        if self.pidfd is not None:
            os.close(self.pidfd)
            self.pidfd = None


class TraceSession:
    """Own tracing across injection AND recovery, then end private descendants.

    --kill-on-exit is mandatory: kernel ptrace ownership confines cleanup to
    this tracer's descendants; no host process discovery or broad kill is used.
    The caller must finish its stability/reply checks before leaving the scope.
    """
    def __init__(self, binary, argv, env, home, cwd, prefix, pass_fds=()):
        self.binary, self.argv, self.env = binary, list(argv), dict(env)
        self.home, self.cwd, self.prefix = home, cwd, Path(prefix)
        self.pass_fds = tuple(pass_fds)
        self.tracer = self.updater = self.stderr = None
        self.tracer_start_ticks = None

    def __enter__(self):
        self.stderr = Path(str(self.prefix) + '.stderr').open('xb')
        os.fchmod(self.stderr.fileno(), 0o600)
        try:
            self.tracer = subprocess.Popen([
                self.binary, '--kill-on-exit', '-I', '1', '-ff', '-ttt', '-s', '65536',
                '-e', 'trace=process', '-o', str(self.prefix), '--', *self.argv],
                env=self.env, cwd=self.cwd, pass_fds=self.pass_fds,
                stderr=self.stderr, umask=0o077)
            self.tracer_start_ticks = identity.birth(self.tracer.pid)
            self.updater = bind_tracee(self.tracer, self.argv, self.home,
                                      self.cwd, self.prefix)
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            if self.tracer is not None and self.tracer.poll() is None:
                if self.tracer_start_ticks is None:
                    raise Refusal('cannot clean tracer without bound identity')
                identity.signal_bound(self.tracer.pid, self.tracer_start_ticks,
                                      signal.SIGTERM)
                try:
                    self.tracer.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    identity.signal_bound(self.tracer.pid, self.tracer_start_ticks,
                                          signal.SIGKILL)
                    self.tracer.wait(timeout=10)
        finally:
            if self.updater is not None:
                self.updater.close()
            if self.stderr is not None:
                self.stderr.close()

    def __exit__(self, exc_type, exc, tb):
        self.close()


def bind_tracee(tracer, argv, home, cwd, prefix, timeout=10):
    """Only inspect direct children of our still-live Popen tracer."""
    if tracer.poll() is not None or psutil.Process(tracer.pid).ppid() != os.getpid():
        raise Refusal('tracer is not a live owned child')
    tracer_birth = identity.birth(tracer.pid)
    deadline = time.monotonic() + timeout
    children_path = Path(f'/proc/{tracer.pid}/task/{tracer.pid}/children')
    while time.monotonic() < deadline:
        if tracer.poll() is not None or identity.birth(tracer.pid) != tracer_birth:
            raise Refusal('tracer identity changed during binding')
        children = children_path.read_text().split()
        if len(children) > 1:
            raise Refusal('ambiguous initial tracer children')
        if not children:
            time.sleep(0.01)
            continue
        pid = int(children[0])
        if pid <= 1 or pid == tracer.pid:
            raise Refusal('invalid tracee identity')
        proc = psutil.Process(pid)
        birth_ticks = identity.birth(pid)
        # strace may still be between fork and the first exec.
        if proc.exe() != str(Path(argv[0]).resolve(strict=True)):
            time.sleep(0.01)
            continue
        def valid():
            uids = proc.uids()
            return (proc.ppid() == tracer.pid and proc.cmdline() == list(argv)
                    and proc.exe() == str(Path(argv[0]).resolve(strict=True))
                    and Path(proc.cwd()).resolve() == Path(cwd).resolve()
                    and proc.environ().get('CLOVER_HOME') == str(home)
                    and (uids.real, uids.effective, uids.saved) == (os.getuid(),) * 3
                    and identity.birth(pid) == birth_ticks)
        if not valid():
            raise Refusal('tracee executable, argv, home or identity mismatch')
        pidfd = os.pidfd_open(pid, 0)
        try:
            if (not valid() or identity.birth(tracer.pid) != tracer_birth
                    or children_path.read_text().split() != [str(pid)]):
                raise Refusal('tracee changed while opening pidfd')
            return BoundTracee(pid, birth_ticks, pidfd, str(prefix) + '.' + str(pid))
        except BaseException:
            os.close(pidfd)
            raise
    raise Refusal('no bound updater appeared under tracer')
