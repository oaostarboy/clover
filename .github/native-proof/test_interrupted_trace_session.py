"""Trace-session ownership tests; execute in private PID/network namespaces."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

import interrupted_traced_updater as m


class SessionTests(unittest.TestCase):
    def test_controller_keeps_tracer_through_recovery_callback(self):
        import interrupted_controller as controller
        import interrupted_identity as identity
        import signal
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            argv = [sys.executable, '-I', '-S', '-c',
                    'import os,time; os.fork(); time.sleep(20)']
            called = []
            def inject(updater):
                time.sleep(0.05)
                identity.signal_bound(updater.pid, updater.birth_ticks, signal.SIGKILL)
                self.assertEqual(updater.wait(timeout=5), -signal.SIGKILL)
                called.append('injected')
                return {'injected': True}
            def recover(session, result):
                self.assertTrue(result['injected'])
                self.assertIsNone(session.tracer.poll())
                called.append('recovered')
                return {'synthetic_only': True}
            result = controller.run_observed_recovery(
                os.environ['RECOVERY_STRACE'], argv,
                {**os.environ, 'CLOVER_HOME': str(home)}, home, home,
                home / 'kernel', (), inject, recover)
            self.assertEqual(called, ['injected', 'recovered'])
            self.assertTrue(result['recovery']['synthetic_only'])
            self.assertGreaterEqual(result['trace']['end_wall_ns'],
                                    result['trace']['start_wall_ns'])

    def test_failed_recovery_still_kills_private_tracee(self):
        import select
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            argv = [sys.executable, '-I', '-S', '-c', 'import time; time.sleep(20)']
            captured = []
            def inject(updater):
                captured.append(os.dup(updater.pidfd))
                return {'injected': True}
            def recover(session, result):
                raise ValueError('synthetic recovery failure')
            import interrupted_controller as controller
            try:
                with self.assertRaisesRegex(ValueError, 'synthetic recovery failure'):
                    controller.run_observed_recovery(os.environ['RECOVERY_STRACE'], argv,
                        {**os.environ, 'CLOVER_HOME': str(home)}, home, home,
                        home / 'kernel', (), inject, recover)
                self.assertTrue(select.select(captured, [], [], 2)[0])
            finally:
                for fd in captured:
                    os.close(fd)

    def test_main_routes_recovery_through_observation(self):
        import inspect
        import interrupted_controller as controller
        source = inspect.getsource(controller.main)
        self.assertIn('run_observed_recovery(', source)
        script = Path(controller.__file__).with_name('posix.sh').read_text()
        interrupted = script.split('if [[ ${NATIVE_INTERRUPTED_RECOVERY:-0} == 1 ]]; then')[-1].split('  fi', 1)[0]
        self.assertNotIn('interrupted_recovery.py', interrupted)
        self.assertNotIn('stage_independence_verify.py', interrupted)

    def test_context_owns_tracer_and_binds_updater(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            argv = [sys.executable, '-I', '-S', '-c', 'import time; time.sleep(20)']
            with m.TraceSession(os.environ['RECOVERY_STRACE'], argv,
                    {**os.environ, 'CLOVER_HOME': str(home)}, home, home,
                    home / 'kernel') as session:
                self.assertNotEqual(session.updater.pid, session.tracer.pid)
                self.assertIsNone(session.updater.poll())
                self.assertIsNone(session.tracer.poll())
                self.assertTrue(session.tracer_start_ticks)
                exit_fd = os.dup(session.updater.pidfd)
                closing = time.monotonic()
            try:
                import select
                self.assertTrue(select.select([exit_fd], [], [], 2)[0],
                                'private tracee survived tracer cleanup')
            finally:
                os.close(exit_fd)
            self.assertLess(time.monotonic() - closing, 2)
            self.assertIsNotNone(session.tracer.poll())
            self.assertIsNone(session.updater.pidfd)


if __name__ == '__main__':
    unittest.main()
