import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import interrupted_identity as identity


class IdentityTests(unittest.TestCase):
    def test_real_child_stop_and_resume(self):
        import signal
        import time
        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            birth = identity.birth(p.pid)
            identity.signal_bound(p.pid, birth, signal.SIGSTOP)
            deadline = time.monotonic() + 3
            while identity.state(p.pid) != 'T':
                if time.monotonic() >= deadline:
                    self.fail('child did not stop')
                time.sleep(.01)
            identity.signal_bound(p.pid, birth, signal.SIGCONT)
            deadline = time.monotonic() + 3
            while identity.state(p.pid) == 'T':
                if time.monotonic() >= deadline:
                    self.fail('child did not resume')
                time.sleep(.01)
        finally:
            os.kill(p.pid, signal.SIGCONT)
            p.terminate()
            p.wait()

    def test_pid_birth_must_match_before_signal(self):
        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            birth = identity.birth(p.pid)
            with self.assertRaises(identity.Refusal):
                identity.signal_bound(p.pid, birth + 1, 0)
            self.assertIsNone(p.poll())
        finally:
            p.terminate()
            p.wait()

if __name__ == '__main__':
    unittest.main()
