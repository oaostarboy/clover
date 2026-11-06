import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

PATH = Path(__file__).with_name('macos_ownership_diagnostic.py')
spec = importlib.util.spec_from_file_location('diagnostic', PATH)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class Tests(unittest.TestCase):
    def test_real_linux_refusal(self):
        if sys.platform != 'linux':
            self.skipTest('Linux refusal control')
        p = subprocess.run([sys.executable, str(PATH)], capture_output=True, text=True, timeout=5)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stdout)['status'], 'refused')

    def test_gate(self):
        self.assertFalse(d.permitted('darwin', {}))
        self.assertTrue(d.permitted('darwin', {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}))

    def test_ancestry_boundary(self):
        self.assertEqual(d.ancestry(3, {3: {'ppid': 2, 'birth': 10}})['boundary_pid'], 2)

    def test_ancestry_denied_parent(self):
        result = d.ancestry(3, {3: {'ppid': {'error': 'AccessDenied'}, 'birth': 10}})
        self.assertFalse(result['cycle'])

    def test_ancestry_cycle(self):
        self.assertTrue(d.ancestry(3, {3: {'ppid': 4}, 4: {'ppid': 3}})['cycle'])

    def test_exact_path_boundaries(self):
        self.assertTrue(d.within('/target/venv/python', '/target'))
        self.assertFalse(d.within('/target-foreign/venv/python', '/target'))

    def test_denied_cmdline_retained_without_secrets(self):
        class Denied(Exception):
            pass
        class Proc:
            pid = 3
            def uids(self): return (501, 0, 0)
            def ppid(self): return 2
            def create_time(self): return 10.0
            def exe(self): return '/usr/bin/sudo'
            def cwd(self): return '/target/private-secret'
            def cmdline(self): raise Denied('secret-token')
        class Fake:
            Error = Denied
            @staticmethod
            def process_iter(): return iter([Proc()])
        with patch.object(d.os, 'getuid', return_value=501):
            result = d.observe(Fake, {'target': Path('/target')})
        encoded = json.dumps(result)
        self.assertNotIn('secret', encoded)
        self.assertFalse(result['processes'][0]['cmdline']['readable'])
        self.assertEqual(result['processes'][0]['uids'], [501, 0, 0])


if __name__ == '__main__':
    unittest.main()
