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
    def test_bootstrap_failure_is_preserved_with_after_diagnostics(self):
        self.assertTrue(hasattr(m, 'attempt_bootstrap'))
        original = RuntimeError('original bootstrap refusal')
        with patch.object(m, 'run', side_effect=original), patch.object(m, 'capture_bootstrap_diagnostics') as capture:
            with self.assertRaises(RuntimeError) as raised:
                m.attempt_bootstrap([], 600, 'io.clover.ci.probe.123', '/Users/cpv123/Library/LaunchAgents/probe.plist')
            self.assertIs(raised.exception, original)
            self.assertEqual([c.args[-1] for c in capture.call_args_list], ['before', 'after'])

    def test_capture_filters_logs_and_survives_private_home_permissions(self):
        import json
        label = 'io.clover.ci.probe.123'
        events = [{'eventMessage': 'unrelated SUPERSECRET'}, {'eventMessage': label + ' rejected password=SUPERSECRET'}]
        def command(args, **kwargs):
            if '/usr/bin/log' in args:
                return subprocess.CompletedProcess(args, 0, json.dumps(events), '')
            if '/usr/bin/python3' in args:
                return subprocess.CompletedProcess(args, 0, json.dumps({'sha256': 'a' * 64, 'paths': []}), '')
            return subprocess.CompletedProcess(args, 5, 'SUPERSECRET', 'SUPERSECRET')
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'E', pathlib.Path(d)), patch.object(m, 'run', side_effect=command), patch.object(pathlib.Path, 'read_bytes', side_effect=PermissionError('SUPERSECRET')):
            m.capture_bootstrap_diagnostics([], 600, label, '/missing/private/probe.plist', 'after')
            raw = (pathlib.Path(d) / 'bootstrap-after.json').read_text()
            result = json.loads(raw)
            self.assertNotIn('SUPERSECRET', raw)
            self.assertEqual(result['plist_read_error_type'], 'PermissionError')
            self.assertEqual(result['owner_metadata']['sha256'], 'a' * 64)
            self.assertEqual(len(result['launchd_messages']), 1)
            self.assertIn('[REDACTED]', result['launchd_messages'][0])
            self.assertEqual(result['queries']['domain']['returncode'], 5)

    def test_diagnostics_never_persist_unrelated_command_output(self):
        with tempfile.TemporaryDirectory() as d:
            plist = pathlib.Path(d) / 'probe.plist'
            plist.write_bytes(b'<plist/>')
            secret = 'SHOULD_NOT_APPEAR_SECRET'
            with patch.object(m, 'run', return_value=subprocess.CompletedProcess([], 5, secret, secret)):
                result = m.bootstrap_diagnostics([], 600, 'io.clover.ci.probe.123', str(plist), 'before')
            self.assertNotIn(secret, repr(result))
            self.assertEqual(result['plist_hex'], b'<plist/>'.hex())
            self.assertEqual(result['queries']['domain']['returncode'], 5)

    def test_help_query_uses_local_directory_source(self):
        help_output = '\n    -read <path>\n    -create <record>\n    -delete <record>\n'
        with patch.object(m, 'run', return_value=subprocess.CompletedProcess([], 0, help_output, '')) as call:
            self.assertTrue(hasattr(m, 'verify_dscl_operations'), 'Missing checked help operation')
            m.verify_dscl_operations()
            self.assertEqual(call.call_args.args[0], ['/usr/bin/dscl', '.', '-help'])

    def test_ordinary_command_selects_domain_and_drops_uid(self):
        self.assertTrue(hasattr(m, 'ordinary_command'))
        self.assertEqual(m.ordinary_command(600, 'cpv123'), ['/usr/bin/sudo', '-n', '/bin/launchctl', 'asuser', '600', '/usr/bin/sudo', '-n', '-H', '-u', 'cpv123'])
        for uid, user in ((0, 'cpv123'), (600, 'runner'), (600, 'cpv123;bad')):
            with self.assertRaises(ValueError):
                m.ordinary_command(uid, user)

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
    def test_directory_failures_refused_before_mutation(self):
        for error in ('eDSUnknownNodeName', 'eDSPermissionError', '', 'eDSRecordNotFound eDSUnknownNodeName'):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as d:
                failure = subprocess.CompletedProcess([], 1, '', error)
                with patch.object(m, 'E', pathlib.Path(d)), patch.object(m, 'native_guard', return_value='123'), patch.object(m, 'run', return_value=failure) as call:
                    with self.assertRaisesRegex(RuntimeError, 'account lookup inconclusive'):
                        m.main()
                    self.assertEqual(call.call_count, 1, 'Must stop before any provisioning command')
                    self.assertNotIn('/usr/bin/sudo', call.call_args.args[0])

    def test_failed_mkdir_never_authorizes_home_removal(self):
        calls = []
        reads = 0
        def command(args, check=True, timeout=20):
            nonlocal reads
            calls.append(args)
            if args[:3] == ['/usr/bin/dscl', '.', '-read']:
                reads += 1
                if 'UniqueID' in args:
                    return subprocess.CompletedProcess(args, 0, 'UniqueID: 600\nNFSHomeDirectory: /Users/cpv123\n', '')
                return subprocess.CompletedProcess(args, 56, '', 'eDSRecordNotFound')
            if args == ['/usr/bin/dscl', '.', '-help']:
                return subprocess.CompletedProcess(args, 0, '\n -read x\n -create x\n -delete x\n', '')
            if args[:3] == ['/usr/bin/dscl', '.', '-list']:
                return subprocess.CompletedProcess(args, 0, 'runner 501\n', '')
            if '/bin/mkdir' in args:
                raise RuntimeError('mkdir collision')
            return subprocess.CompletedProcess(args, 0, '', '')
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'save'), patch.object(m, 'native_guard', return_value='123'), patch.object(m, 'run', side_effect=command), patch.object(pathlib.Path, 'exists', side_effect=[False, True, True]), patch.object(pathlib.Path, 'is_symlink', return_value=False), patch.object(pathlib.Path, 'stat', return_value=SimpleNamespace(st_uid=600)):
            with self.assertRaises(RuntimeError):
                m.main()
        self.assertFalse(any('/bin/rm' in c for c in calls), 'A failed mkdir must never grant deletion authority')

    def test_cleanup_confirmation_requires_absence_not_errors_or_symlinks(self):
        self.assertTrue(hasattr(m, 'account_and_home_absent'))
        absent = subprocess.CompletedProcess([], 56, '', 'eDSRecordNotFound')
        mixed = subprocess.CompletedProcess([], 56, '', 'eDSRecordNotFound eDSUnknownNodeName')
        with tempfile.TemporaryDirectory() as d:
            home = pathlib.Path(d) / 'home'
            self.assertTrue(m.account_and_home_absent(absent, home))
            self.assertFalse(m.account_and_home_absent(mixed, home))
            self.assertFalse(m.account_and_home_absent(subprocess.CompletedProcess([], 0, '', ''), home))
            home.symlink_to(pathlib.Path(d) / 'missing')
            self.assertFalse(m.account_and_home_absent(absent, home), 'Dangling symlink is not absent')

    def test_existing_home_refused_before_mutation(self):
        miss = subprocess.CompletedProcess([], 1, '', 'eDSRecordNotFound')
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'E', pathlib.Path(d)), patch.object(m, 'native_guard', return_value='123'), patch.object(m, 'run', return_value=miss) as run, patch.object(pathlib.Path, 'exists', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'home collision'): m.main()
            self.assertEqual(run.call_count, 1)

if __name__ == '__main__': unittest.main()
