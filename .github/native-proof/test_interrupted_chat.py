import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.request
from unittest.mock import patch
from stage_independence_wire import ChatWire

class FreshNonceTest(unittest.TestCase):
    def test_model_echoes_recovery_nonce(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'NATIVE_INTERRUPTED_RECOVERY':'1'}):
            wire = ChatWire(Path(d), Path(d)/'effect'); wire.start()
            try:
                nonce='RECOVERY_NONCE_' + 'a'*32
                body=json.dumps({'messages':[{'role':'user','content':'STAGE_INDEPENDENCE_TURN ' + nonce}]}).encode()
                req=urllib.request.Request('http://127.0.0.1:%d/v1/chat/completions' % wire.model.server_address[1], body, {'Content-Type':'application/json'})
                with urllib.request.urlopen(req, timeout=3) as response: data=json.load(response)
                self.assertIn(nonce, data['choices'][0]['message']['content'])
            finally: wire.close()

if __name__ == '__main__': unittest.main()
