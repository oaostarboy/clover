import importlib.util
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

p = pathlib.Path(__file__).with_name('macos_user_isolation_probe.py')
spec = importlib.util.spec_from_file_location('probe', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class GuardTests(unittest.TestCase):
    def test_linux_and_nonhosted_refused(self):
        with patch.object(m.sys, 'platform', 'linux'), patch.dict(os.environ, {'GITHUB_ACTIONS':'true', 'RUNNER_ENVIRONMENT':'github-hosted'}, clear=True):
            with self.assertRaises(RuntimeError): m.native_guard()
        with patch.object(m.sys, 'platform', 'darwin'), patch.dict(os.environ, {'GITHUB_ACTIONS':'true', 'RUNNER_ENVIRONMENT':'self-hosted'}, clear=True):
            with self.assertRaises(RuntimeError): m.native_guard()
    def test_unsafe_temp_and_run_id_refused(self):
        with patch.object(m.sys, 'platform', 'darwin'), patch.dict(os.environ, {'GITHUB_ACTIONS':'true', 'RUNNER_ENVIRONMENT':'github-hosted', 'RUNNER_TEMP':'relative', 'GITHUB_RUN_ID':'1'}, clear=True):
            with self.assertRaises(RuntimeError): m.native_guard()
        with tempfile.TemporaryDirectory() as d, patch.object(m.sys, 'platform', 'darwin'), patch.dict(os.environ, {'GITHUB_ACTIONS':'true', 'RUNNER_ENVIRONMENT':'github-hosted', 'RUNNER_TEMP':d, 'GITHUB_RUN_ID':'../bad'}, clear=True):
            with self.assertRaises(RuntimeError): m.native_guard()
    def test_existing_account_refused_before_mutation(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'E', pathlib.Path(d)), patch.object(m, 'native_guard', return_value='123'), patch.object(m, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            with self.assertRaisesRegex(RuntimeError, 'account already exists'): m.main()
            self.assertEqual(run.call_count, 1)
    def test_existing_home_refused_before_mutation(self):
        miss = subprocess.CompletedProcess([], 1, '', 'eDSRecordNotFound')
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'E', pathlib.Path(d)), patch.object(m, 'native_guard', return_value='123'), patch.object(m, 'run', return_value=miss) as run, patch.object(pathlib.Path, 'exists', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'home collision'): m.main()
            self.assertEqual(run.call_count, 1)

if __name__ == '__main__': unittest.main()
