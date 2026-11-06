import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import interrupted_identity as identity
import interrupted_schedule as schedule


class ScheduleTests(unittest.TestCase):
    def child(self):
        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(self.finish, p)
        return p, identity.birth(p.pid)

    @staticmethod
    def finish(p):
        if p.poll() is None:
            os.kill(p.pid, signal.SIGCONT)
            p.terminate()
            p.wait(timeout=5)

    def test_refuses_missing_watcher(self):
        w = self.child()
        with self.assertRaises(schedule.Refusal):
            with schedule.frozen_watchers([w]):
                pass
        self.assertNotEqual(identity.state(w[0].pid), 'T')

    def test_cleanup_resumes_both_after_exception(self):
        a, b = self.child(), self.child()
        with self.assertRaisesRegex(ValueError, 'failure'):
            with schedule.frozen_watchers([a, b]):
                self.assertEqual(identity.state(a[0].pid), 'T')
                self.assertEqual(identity.state(b[0].pid), 'T')
                raise ValueError('failure')
        for p, birth in (a, b):
            end = time.monotonic() + 2
            while identity.state(p.pid) == 'T' and time.monotonic() < end:
                time.sleep(.01)
            self.assertNotEqual(identity.state(p.pid), 'T')

    def test_resume_attempts_all_watchers_even_if_first_signal_fails(self):
        from unittest.mock import patch
        a, b = self.child(), self.child()
        real_signal = identity.signal_bound
        resumed = []
        def fail_first(pid, birth, sig):
            if sig == signal.SIGCONT:
                resumed.append(pid)
                if pid == a[0].pid:
                    raise PermissionError('synthetic denied')
            return real_signal(pid, birth, sig)
        with patch.object(identity, 'signal_bound', side_effect=fail_first):
            with self.assertRaises(schedule.Refusal):
                with schedule.frozen_watchers([a,b]):
                    pass
        self.assertEqual(resumed, [a[0].pid,b[0].pid])

    def test_harmless_two_watcher_crash_schedule(self):
        old, updater, a, b = self.child(), self.child(), self.child(), self.child()
        with schedule.frozen_watchers([a, b]):
            old[0].terminate()
            old[0].wait(timeout=5)
            schedule.check_boundary(old, [])
            identity.signal_bound(updater[0].pid, updater[1], signal.SIGKILL)
            self.assertEqual(updater[0].wait(timeout=5), -signal.SIGKILL)
            self.assertEqual(identity.state(a[0].pid), 'T')
            self.assertEqual(identity.state(b[0].pid), 'T')
        for proc, _ in (a, b):
            end = time.monotonic() + 2
            while identity.state(proc.pid) == 'T' and time.monotonic() < end:
                time.sleep(.01)
            self.assertNotEqual(identity.state(proc.pid), 'T')

    def test_refuses_old_alive_or_replacement_present(self):
        old = self.child()
        with self.assertRaises(schedule.Refusal):
            schedule.check_boundary(old, [])
        old[0].terminate(); old[0].wait()
        replacement = self.child()
        with self.assertRaises(schedule.Refusal):
            schedule.check_boundary(old, [replacement])
        schedule.check_boundary(old, [])

if __name__ == '__main__':
    unittest.main()
