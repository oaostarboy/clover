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
from interrupted_controller import execute_fault

ROOT = Path(__file__).parent


class ProtocolTest(unittest.TestCase):
    def test_real_owned_processes_stop_crash_and_resume(self):
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
            try:
                result = execute_fault(stream, updater, old.pid, old_birth, old_argv,
                                       root, root, beacon, port=0, tracked=owned)
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
