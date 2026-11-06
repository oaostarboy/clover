import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parent
WRAPPER = ROOT / 'interrupted_trace.py'


class TraceTests(unittest.TestCase):
    def test_real_wrapper_separates_tracee_and_tracer_stderr(self):
        import interrupted_traced_updater as traced
        import interrupted_observation as observation
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            gateway = p / 'gateway.py'; caller = p / 'caller.py'; entry = p / 'entry.py'
            gateway.write_text('def prepare():\n return "detached"\ndef wait():\n return None\n')
            caller.write_text('import gateway\ndef update():\n gateway.prepare()\n gateway.wait()\n')
            entry.write_text('import sys, subprocess\nsys.path.insert(0, '+repr(d)+')\nimport caller\nprint("PARENT-MARKER", file=sys.stderr)\nsubprocess.run([sys.executable, "-c", "import sys; print(\\\"CHILD-MARKER\\\", file=sys.stderr)"])\ncaller.update()\n')
            parent, child = socket.socketpair()
            logfd = os.open(p / 'tracee.log', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            cfg = {k: str(v) for k, v in [('entry', entry), ('gateway', gateway), ('caller', caller)]}
            cfg.update({k + '_hash': hashlib.sha256(Path(cfg[k]).read_bytes()).hexdigest() for k in ('entry', 'gateway', 'caller')})
            cfg.update(home=d, fd=child.fileno(), stderr_fd=logfd, prepare='prepare', wait='wait', caller_function='update')
            config = p / 'config.json'; config.write_text(json.dumps(cfg))
            prefix = p / 'trace'
            try:
                with traced.TraceSession(os.environ['RECOVERY_STRACE'], [sys.executable, '-I', '-S', str(WRAPPER), str(config), '--'],
                                         {**os.environ, 'CLOVER_HOME': d}, d, d, prefix, (child.fileno(), logfd)) as session:
                    child.close()
                    stream = parent.makefile('rwb', buffering=0)
                    stream.write(f'bind {session.updater.pid}\n'.encode())
                    for stage in ('prepare', 'wait'):
                        self.assertEqual(json.loads(stream.readline())['stage'], stage)
                        stream.write(b'continue\n')
                    import time
                    deadline = time.monotonic() + 10
                    while session.tracer.poll() is None and time.monotonic() < deadline: time.sleep(.01)
                log = (p / 'tracee.log').read_text()
                self.assertIn('PARENT-MARKER', log); self.assertIn('CHILD-MARKER', log)
                stderr = Path(str(prefix) + '.stderr').read_bytes()
                self.assertNotIn(b'MARKER', stderr)
                pids = {int(x.name.rsplit('.', 1)[1]) for x in p.glob('trace.*') if x.name.rsplit('.', 1)[1].isdigit()}
                observation._check_stderr(stderr, pids, session.binary)
                self.assertGreaterEqual(len(pids), 2)
            finally:
                os.close(logfd); parent.close(); child.close()

    def test_unsafe_tracee_stderr_descriptors_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); config = p / 'config.json'
            parent, child = socket.socketpair()
            path = p / 'public'; path.touch(mode=0o644); path.chmod(0o644)
            fd = os.open(path, os.O_WRONLY)
            try:
                for candidate in (0, child.fileno(), fd):
                    cfg = {'fd': child.fileno(), 'stderr_fd': candidate}
                    config.write_text(json.dumps(cfg))
                    proc = subprocess.run([sys.executable, '-I', '-S', str(WRAPPER), str(config), '--'],
                                          pass_fds=(child.fileno(), fd), capture_output=True, text=True, timeout=5)
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn('tracee stderr fd', proc.stderr)
            finally:
                os.close(fd); parent.close(); child.close()

    def test_only_exact_pinned_target_alias_with_matching_bytes_is_allowed(self):
        from interrupted_trace import source_matches
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stage = root / 'stage.py'; alias = root / 'target.py'; other = root / 'other.py'
            stage.write_text('safe'); alias.write_text('safe'); other.write_text('safe')
            digest = hashlib.sha256(stage.read_bytes()).hexdigest()
            self.assertTrue(source_matches(str(stage), str(stage), digest, str(alias)))
            self.assertTrue(source_matches(str(alias), str(stage), digest, str(alias)))
            self.assertFalse(source_matches(str(other), str(stage), digest, str(alias)))
            alias.write_text('changed')
            self.assertFalse(source_matches(str(alias), str(stage), digest, str(alias)))

    def test_wrong_source_hash_refused_before_entry(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            source = p / 'entry.py'
            source.write_text('raise Exception("entry must not run")\n')
            cfg = {'entry': str(source), 'entry_hash': '0' * 64}
            configpath = p / 'config.json'; configpath.write_text(json.dumps(cfg))
            proc = subprocess.run([sys.executable, '-I', '-S', str(WRAPPER), str(configpath), '--'],
                                  capture_output=True, text=True, timeout=5)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('source hash mismatch', proc.stderr)
            self.assertNotIn('entry must not run', proc.stderr)

    def test_wrong_home_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            source = p / 'entry.py'
            source.write_text('raise Exception("entry must not run")\n')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            cfg = {k: str(source) for k in ('entry', 'gateway', 'caller')}
            cfg.update({k + '_hash': digest for k in ('entry', 'gateway', 'caller')})
            cfg['home'] = d
            configpath = p / 'config.json'; configpath.write_text(json.dumps(cfg))
            proc = subprocess.run([sys.executable, '-I', '-S', str(WRAPPER), str(configpath), '--'],
                                  env={**os.environ, 'CLOVER_HOME': d + '/wrong'},
                                  capture_output=True, text=True, timeout=5)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('home mismatch', proc.stderr)

    def test_wrong_pid_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); source = p / 'entry.py'; source.write_text('pass\n')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            parent, child = socket.socketpair()
            cfg = {k: str(source) for k in ('entry', 'gateway', 'caller')}
            cfg.update({k + '_hash': digest for k in ('entry', 'gateway', 'caller')})
            cfg.update(home=d, fd=child.fileno())
            configpath = p / 'config.json'; configpath.write_text(json.dumps(cfg))
            proc = subprocess.Popen([sys.executable, '-I', '-S', str(WRAPPER), str(configpath), '--'],
                                    env={**os.environ, 'CLOVER_HOME': d}, pass_fds=(child.fileno(),),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            child.close()
            try:
                parent.sendall(b'bind 999999999\n')
                out, err = proc.communicate(timeout=5)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('PID binding mismatch', err)
            finally:
                parent.close()
                if proc.poll() is None: proc.kill(); proc.wait()

    def test_two_real_frame_return_barriers_and_entry_argv(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            gateway = p / 'gateway.py'
            caller = p / 'update_cmd.py'
            entry = p / 'entry.py'
            gateway.write_text('def prepare():\n    return "detached"\ndef wait():\n    return None\n')
            caller.write_text('import gateway\ndef update():\n    gateway.prepare()\n    gateway.wait()\n')
            entry.write_text('import sys\nsys.path.insert(0, ' + repr(d) + ')\nimport update_cmd\nupdate_cmd.update()\nprint("ARGS", sys.argv[1:])\n')
            parent, child = socket.socketpair()
            config = {'entry': str(entry), 'entry_hash': hashlib.sha256(entry.read_bytes()).hexdigest(),
                      'gateway': str(gateway), 'gateway_hash': hashlib.sha256(gateway.read_bytes()).hexdigest(),
                      'caller': str(caller), 'caller_hash': hashlib.sha256(caller.read_bytes()).hexdigest(),
                      'home': d, 'pid': 0, 'fd': child.fileno(),
                      'prepare': 'prepare', 'wait': 'wait', 'caller_function': 'update'}
            configpath = p / 'config.json'; configpath.write_text(json.dumps(config))
            env = {**os.environ, 'CLOVER_HOME': d}
            proc = subprocess.Popen([sys.executable, '-I', '-S', str(WRAPPER), str(configpath), '--', 'one', 'two'],
                                    env=env, pass_fds=(child.fileno(),), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            child.close()
            try:
                parent.settimeout(5)
                f = parent.makefile('rwb', buffering=0)
                f.write(f'bind {proc.pid}\n'.encode())
                for stage in ('prepare', 'wait'):
                    event = json.loads(f.readline())
                    self.assertEqual(event['stage'], stage)
                    self.assertEqual(event['pid'], proc.pid)
                    f.write(b'continue\n')
                out, err = proc.communicate(timeout=5)
                self.assertEqual(proc.returncode, 0, err)
                self.assertIn("ARGS ['one', 'two']", out)
            finally:
                if proc.poll() is None:
                    proc.kill(); proc.wait()
                parent.close()

if __name__ == '__main__':
    unittest.main()
