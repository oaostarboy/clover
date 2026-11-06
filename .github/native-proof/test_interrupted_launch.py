import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

WRAPPER = Path(__file__).with_name('interrupted_trace.py')

class LaunchTraceTests(unittest.TestCase):
    def test_records_only_pinned_product_launches_before_barriers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            gateway=root/'gateway.py'; caller=root/'caller.py'; entry=root/'entry.py'
            gateway.write_text('import subprocess,sys\ndef spawn():\n p=subprocess.Popen([sys.executable,"-c","pass"])\n p.wait()\ndef prepare():\n spawn()\n return "detached"\ndef wait():\n return None\n')
            caller.write_text('import gateway\ndef update():\n gateway.prepare()\n gateway.wait()\n')
            entry.write_text(f'import sys\nsys.path.insert(0,{tmp!r})\nimport caller\ncaller.update()\n')
            parent, child=socket.socketpair(); parent.settimeout(5)
            cfg={k:str(v) for k,v in dict(entry=entry,gateway=gateway,caller=caller).items()}
            cfg.update({k+'_hash':hashlib.sha256(Path(cfg[k]).read_bytes()).hexdigest() for k in ['entry','gateway','caller']})
            cfg.update(home=tmp,fd=child.fileno(),prepare='prepare',wait='wait',caller_function='update',launch_functions={'gateway':['spawn'],'caller':['update']})
            config=root/'config.json';config.write_text(json.dumps(cfg))
            proc=subprocess.Popen([sys.executable,'-I','-S',str(WRAPPER),str(config),'--'],env={**os.environ,'CLOVER_HOME':tmp},pass_fds=(child.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            child.close();stream=parent.makefile('rwb',buffering=0)
            try:
                stream.write(f'bind {proc.pid}\n'.encode())
                events=[]
                for _ in range(4):
                    line=stream.readline()
                    if not line:break
                    event=json.loads(line);events.append(event)
                    if event['stage'] in ('prepare','wait'):stream.write(b'continue\n')
                    if event['stage']=='wait':break
                stdout,stderr=proc.communicate(timeout=5)
                self.assertEqual(proc.returncode,0,stderr)
                self.assertEqual([x['stage'] for x in events],['launch','prepare','wait'])
                self.assertGreater(events[0]['child_pid'],1)
                self.assertEqual(events[0]['argv'][1:],['-c','pass'])
            finally:
                stream.close();parent.close()
                if proc.poll() is None:proc.kill();proc.communicate()

if __name__=='__main__':unittest.main()
