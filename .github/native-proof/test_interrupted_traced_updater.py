"""Real descendant identity tests; run inside a private PID namespace."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

import interrupted_identity as identity
import interrupted_traced_updater as traced


class TracedUpdaterTests(unittest.TestCase):
    def test_rejects_home_mismatch_for_real_tracee(self):
        tracer_bin = os.environ['RECOVERY_STRACE']
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            argv = [sys.executable, '-I', '-S', '-c', 'import time; time.sleep(20)']
            prefix = home / 'process'
            tracer = subprocess.Popen([tracer_bin, '-ff', '-ttt', '-e', 'trace=process',
                '-o', str(prefix), '--', *argv], cwd=home,
                env={**os.environ, 'CLOVER_HOME': str(home)},
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            updater = None
            try:
                updater = traced.bind_tracee(tracer, argv, home, home, prefix, timeout=5)
                with self.assertRaises(identity.Refusal):
                    traced.bind_tracee(tracer, argv, home / 'wrong', home, prefix, timeout=5)
                with self.assertRaises(identity.Refusal):
                    traced.bind_tracee(tracer, [*argv, 'unexpected'], home, home, prefix, timeout=5)
            finally:
                if updater is not None:
                    identity.signal_bound(updater.pid, updater.birth_ticks, signal.SIGKILL)
                    updater.wait(timeout=5)
                    updater.close()
                if tracer.poll() is None:
                    tracer.terminate()
                tracer.wait(timeout=5)
                tracer.stderr.close()

    def test_tracing_survives_updater_death_and_records_child_exec(self):
        tracer_bin = os.environ['RECOVERY_STRACE']
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            prefix = home / 'process'
            ready = home / 'ready'
            go = home / 'go'
            code = (f'import os,time,pathlib\np=os.fork()\nif p == 0:\n'
                    f' pathlib.Path({str(ready)!r}).touch()\n'
                    f' while not pathlib.Path({str(go)!r}).exists(): time.sleep(.01)\n'
                    ' os.execl("/usr/bin/true", "true")\nelse:\n time.sleep(20)')
            argv = [sys.executable, '-I', '-S', '-c', code]
            tracer = subprocess.Popen([tracer_bin, '-ff', '-ttt', '-e', 'trace=process',
                '-o', str(prefix), '--', *argv], cwd=home,
                env={**os.environ, 'CLOVER_HOME': str(home)},
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            updater = None
            try:
                updater = traced.bind_tracee(tracer, argv, home, home, prefix, timeout=5)
                import time
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists())
                identity.signal_bound(updater.pid, updater.birth_ticks, signal.SIGKILL)
                self.assertEqual(updater.wait(timeout=5), -signal.SIGKILL)
                self.assertIsNone(tracer.poll())
                go.touch()
                tracer.wait(timeout=5)
                traces = [p.read_text() for p in home.glob('process.*')]
                self.assertTrue(any('execve("/usr/bin/true"' in text for text in traces))
            finally:
                go.touch()
                if updater is not None:
                    if updater.poll() is None:
                        identity.signal_bound(updater.pid, updater.birth_ticks, signal.SIGKILL)
                    updater.close()
                if tracer.poll() is None:
                    tracer.terminate()
                tracer.wait(timeout=5)
                tracer.stderr.close()

    def test_exit_requires_terminal_trace_record(self):
        with tempfile.TemporaryDirectory() as d:
            read_fd, write_fd = os.pipe()
            os.close(write_fd)  # A readable handle represents an exited tracee.
            path = Path(d) / 'trace'
            updater = traced.BoundTracee(42, 1, read_fd, path)
            try:
                self.assertIsNone(updater.poll())
                path.write_text('123.0 +++ exited with 0 +++\n123.1 execve("x", [], []) = 0\n')
                self.assertIsNone(updater.poll())
                path.write_text('123.0 +++ killed by SIGNOTREAL +++\n')
                with self.assertRaises(identity.Refusal):
                    updater.poll()
            finally:
                updater.close()

    def test_binds_tracee_not_tracer_and_reads_kernel_kill_status(self):
        tracer_bin = os.environ['RECOVERY_STRACE']
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            argv = [sys.executable, '-I', '-S', '-c', 'import time; time.sleep(20)']
            prefix = home / 'process'
            tracer = subprocess.Popen([tracer_bin, '-ff', '-ttt', '-e', 'trace=process',
                '-o', str(prefix), '--', *argv], cwd=home,
                env={**os.environ, 'CLOVER_HOME': str(home)},
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            updater = None
            try:
                updater = traced.bind_tracee(tracer, argv, home, home, prefix, timeout=5)
                self.assertNotEqual(updater.pid, tracer.pid)
                self.assertEqual(Path('/proc') .joinpath(str(updater.pid), 'exe').resolve(),
                                 Path(sys.executable).resolve())
                self.assertIsNone(updater.poll())
                identity.signal_bound(updater.pid, updater.birth_ticks, signal.SIGKILL)
                self.assertEqual(updater.wait(timeout=5), -signal.SIGKILL)
                self.assertEqual(updater.returncode, -signal.SIGKILL)
                tracer.wait(timeout=5)
            finally:
                if updater is not None:
                    updater.close()
                # This is the Popen child owned by this synthetic test only.
                if tracer.poll() is None:
                    tracer.terminate()
                    tracer.wait(timeout=5)
                tracer.stderr.close()


if __name__ == '__main__':
    unittest.main()
