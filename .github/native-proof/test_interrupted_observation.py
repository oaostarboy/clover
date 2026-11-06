"""Real tracer/controller accounting through the owned synthetic recovery flow."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import interrupted_controller as controller
import interrupted_identity as identity

SCRIPT = '''import json,os,pathlib,subprocess,sys,time\nroot=pathlib.Path(sys.argv[2]); mode=sys.argv[1]\nif mode == 'updater':\n p=[subprocess.Popen([sys.executable,'-I','-S',__file__,m,str(root)]) for m in ('watcher','other')]\n (root/'watchers').write_text(json.dumps([x.pid for x in p]))\n time.sleep(30)\nelif mode in ('watcher','other'):\n while not (root/'go').exists(): time.sleep(.01)\n if mode == 'watcher':\n  subprocess.run(['git','rev-parse','HEAD'],cwd=root,check=True,stdout=subprocess.DEVNULL)\n  for n in range(int((root/'count').read_text())):\n   subprocess.Popen([sys.executable,'-I','-S',__file__,'gateway',str(root)])\nelse:\n (root/('gateway-'+str(os.getpid()))).write_text('ready')\n time.sleep(30)\n'''


class ControllerAccountingTests(unittest.TestCase):
    def test_tracer_stderr_rejects_detach_and_errors(self):
        import interrupted_observation as observation
        self.assertEqual(observation._check_stderr(b'strace: Process 7 attached\n', {7}), [7])
        for raw in (b'strace: Process 7 detached\n', b'strace: ptrace error\n',
                    b'strace: Process 8 attached\n', b'strace: Process 7 attached'):
            with self.subTest(raw=raw), self.assertRaises(identity.Refusal):
                observation._check_stderr(raw, {7})

    def test_untraced_process_cannot_be_accepted_as_successor(self):
        import interrupted_observation as observation
        with self.assertRaisesRegex(identity.Refusal, 'not attached'):
            observation._assert_traced(os.getpid(), 999999)

    def run_fixture(self, count):
        import interrupted_observation as observation
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); script = root / 'fixture.py'; script.write_text(SCRIPT)
            (root / 'venv/bin').mkdir(parents=True)
            (root / 'venv/bin/python').symlink_to(sys.executable)
            (root / 'count').write_text(str(count))
            git = shutil.which('git'); assert git
            subprocess.run([git, 'init', '-q', d], check=True)
            subprocess.run([git, '-C', d, '-c', 'user.name=Fixture', '-c',
                            'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture'], check=True)
            argv = [sys.executable, '-I', '-S', str(script), 'updater', d]
            gateway_argv = [sys.executable, '-I', '-S', str(script), 'gateway', d]
            def inject(updater):
                deadline = time.monotonic() + 5
                while not (root / 'watchers').exists():
                    if time.monotonic() > deadline: self.fail('watchers did not start')
                    time.sleep(.01)
                pids = json.loads((root / 'watchers').read_text())
                watchers = {str(i): {'pid': pid, 'birth_ticks': identity.birth(pid)} for i, pid in enumerate(pids)}
                identity.signal_bound(updater.pid, updater.birth_ticks, signal.SIGKILL)
                self.assertEqual(updater.wait(timeout=5), -signal.SIGKILL)
                (root / 'go').touch()
                return {'injected': True, 'watchers': watchers}
            def recover(session, fault):
                deadline = time.monotonic() + 5
                while True:
                    gateways = sorted(root.glob('gateway-*'))
                    if len(gateways) >= count: break
                    if time.monotonic() > deadline: self.fail('gateway did not start')
                    time.sleep(.01)
                gateway_pid = int(gateways[0].name.split('-')[1])
                state = root / 'after.json'
                state.write_text(json.dumps({'pid': gateway_pid, 'start_time': -1}))
                with self.assertRaisesRegex(identity.Refusal, 'after-state does not identify'):
                    observation.bind_successor(state, root, root)
                state.write_text(json.dumps({'pid': gateway_pid, 'start_time': identity.birth(gateway_pid)}))
                bound = observation.bind_successor(state, root, root)
                self.assertEqual(bound['pid'], gateway_pid)
                self.assertEqual(bound['argv'], gateway_argv)
                rule = {'parent_pid': fault['watchers']['0']['pid'], 'path': git,
                        'argv': ['git', 'rev-parse', 'HEAD'], 'max_count': 1}
                return controller.finish_accounting(session, fault, root, root, state,
                                                    [rule], root / 'snapshot', timeout=5)
            return controller.run_observed_recovery(os.environ['RECOVERY_STRACE'], argv,
                {**os.environ, 'CLOVER_HOME': d}, root, root, root / 'trace', (), inject, recover)

    def test_one_gateway_and_real_utility_account_before_cleanup(self):
        result = self.run_fixture(1)
        self.assertEqual(result['recovery']['accounting']['launch_count'], 1)
        self.assertEqual(result['recovery']['accounting']['utility_counts'], [1])

    def test_duplicate_gateway_is_rejected_before_cleanup(self):
        with self.assertRaisesRegex(identity.Refusal, 'unexplained, live, or descendant-spawning utility branch'):
            self.run_fixture(2)
