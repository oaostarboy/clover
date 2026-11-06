"""Exercise the scheduler against real, disposable owned processes only."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

import psutil
from interrupted_identity import birth
from interrupted_controller import execute_fault, FaultProgress

ROOT = Path(__file__).parent


class ProtocolTest(unittest.TestCase):
    def test_real_owned_processes_stop_crash_and_resume(self):
        self.exercise_protocol()

    def test_fault_failure_receipts_follow_real_protocol(self):
        for mode, injected in [('signal', None), ('wait', None),
                               ('write_before', False), ('write_after', True),
                               ('resume_cleanup', True), ('recovery', True)]:
            with self.subTest(mode=mode):
                self.exercise_protocol(mode, injected)

    def exercise_protocol(self, mode=None, expected_injected=None):
        from contextlib import ExitStack, contextmanager
        from unittest.mock import patch
        import signal
        import interrupted_identity as identity
        import interrupted_schedule as schedule
        from interrupted_controller import write_failure_receipt
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            gateway = root / 'gateway.py'
            caller = root / 'caller.py'
            entry = root / 'entry.py'
            beacon = root / '.clover-update-heartbeat.json'
            old = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],
                                   env={**os.environ, 'CLOVER_HOME': d}, cwd=d)
            old_birth = birth(old.pid)
            old_argv = psutil.Process(old.pid).cmdline()
            gateway.write_text('''import subprocess,sys\ndef _spawn_gateway_restart_watcher(old):\n return subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)',str(old)])\ndef prepare(old):\n _spawn_gateway_restart_watcher(old)\n return 'detached'\ndef wait():\n return True\n''')
            caller.write_text('''import gateway,subprocess,sys,os,json,time\ndef _cmd_update_impl(old, beacon, argv, home):\n data={'updater_pid':os.getpid(),'updater_create_time':__import__('psutil').Process().create_time(),'cwd':home,'repo':home,'gateway_argv':argv}\n open(beacon,'w').write(json.dumps(data))\n subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)',beacon])\n gateway.prepare(old)\n os.kill(old, 15)\n time.sleep(.3)\n gateway.wait()\n''')
            # -I -S does not expose psutil; explicitly include the ordinary test environment.
            package_root = str(Path(psutil.__file__).parent.parent)
            entry.write_text(f'import sys\nsys.path[:0]=[{d!r},{package_root!r}]\nimport caller\ncaller._cmd_update_impl({old.pid},{str(beacon)!r},{old_argv!r},{d!r})\n')
            parent, child = socket.socketpair()
            cfg = {k: str(v) for k, v in {'entry': entry, 'caller': caller, 'gateway': gateway}.items()}
            cfg.update({k + '_hash': hashlib.sha256(Path(cfg[k]).read_bytes()).hexdigest() for k in ('entry', 'caller', 'gateway')})
            cfg.update(home=d, fd=child.fileno(), prepare='prepare', wait='wait', caller_function='_cmd_update_impl', launch_functions={'caller':['_cmd_update_impl'], 'gateway':['_spawn_gateway_restart_watcher']})
            config = root/'config.json'; config.write_text(json.dumps(cfg))
            updater = subprocess.Popen([sys.executable,'-I','-S',str(ROOT/'interrupted_trace.py'),str(config),'--'],
                                       env={**os.environ,'CLOVER_HOME':d}, cwd=d,
                                       pass_fds=(child.fileno(),), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            child.close(); parent.settimeout(10); stream = parent.makefile('rwb', buffering=0)
            stream.write(f'bind {updater.pid}\n'.encode())
            thread = threading.Thread(target=old.wait, daemon=True); thread.start()
            owned = []
            checked = []
            progress = FaultProgress(root)
            try:
                with ExitStack() as patches:
                    triggered = []
                    real_signal, real_wait = identity.signal_bound, updater.wait
                    real_persist, real_freeze = progress.persist, schedule.frozen_watchers
                    def signal_once(pid, ticks, sig):
                        if mode == 'signal' and pid == updater.pid and sig == signal.SIGKILL and not triggered:
                            triggered.append(mode)
                            raise OSError('injected signal failure')
                        return real_signal(pid, ticks, sig)
                    def wait_once(*a, **kw):
                        if mode == 'wait' and not triggered:
                            triggered.append(mode)
                            raise subprocess.TimeoutExpired('private-updater', .01)
                        return real_wait(*a, **kw)
                    def persist_once():
                        phase = 'attempted' if mode == 'write_before' else 'confirmed'
                        if mode in ('write_before', 'write_after') and progress.phase == phase and not triggered:
                            triggered.append(mode)
                            raise OSError('injected receipt disk failure')
                        return real_persist()
                    @contextmanager
                    def freeze_once(*a, **kw):
                        with real_freeze(*a, **kw):
                            yield
                        if mode == 'resume_cleanup':
                            triggered.append(mode)
                            raise OSError('injected resume cleanup reporting failure')
                    patches.enter_context(patch.object(identity, 'signal_bound', side_effect=signal_once))
                    patches.enter_context(patch.object(updater, 'wait', side_effect=wait_once))
                    patches.enter_context(patch.object(progress, 'persist', side_effect=persist_once))
                    patches.enter_context(patch.object(schedule, 'frozen_watchers', freeze_once))
                    try:
                        result = execute_fault(stream, updater, old.pid, old_birth, old_argv,
                                               root, root, beacon, port=0, tracked=owned,
                                               frozen_check=lambda bindings: checked.append(bindings), progress=progress)
                        if mode == 'recovery':
                            triggered.append(mode)
                            (root / 'interrupted-fault.json').write_text(json.dumps(result))
                            raise RuntimeError('injected recovery verification failure')
                    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
                        if mode is None:
                            raise
                        self.assertEqual(triggered, [mode])
                        write_failure_receipt(root, progress, exc)
                        receipt = json.loads((root / 'interrupted-fault-failure.json').read_text())
                        self.assertIs(receipt['injected'], expected_injected)
                        self.assertIsNotNone(updater.poll())
                        if mode == 'write_before':
                            self.assertEqual(receipt['cleanup'], 'confirmed')
                        if mode == 'recovery':
                            self.assertIs(json.loads((root / 'interrupted-fault.json').read_text())['injected'], receipt['injected'])
                        return
                    self.assertIsNone(mode, 'failure injection did not execute')
                self.assertIs(json.loads((root / 'interrupted-fault-progress.json').read_text())['injected'], True)
                self.assertEqual(len(checked), 1)
                self.assertEqual(set(checked[0]), {'rollback', 'restart'})
                self.assertEqual(updater.returncode, -9)
                self.assertEqual([e['stage'] for e in result['events'] if e['stage'] in ('watchers_stopped','updater_killed','watchers_resumed')],
                                 ['watchers_stopped','updater_killed','watchers_resumed'])
                self.assertEqual(len(owned),2)
                self.assertTrue(all(psutil.Process(p).status() not in ('stopped','tracing-stop') for p in owned))
            finally:
                stream.close(); parent.close()
                if updater.poll() is None: updater.kill(); updater.wait()
                if old.poll() is None: old.kill(); old.wait()
                for pid in owned:
                    try: psutil.Process(pid).kill()
                    except psutil.NoSuchProcess: pass
                updater.communicate()

if __name__ == '__main__': unittest.main()
