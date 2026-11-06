import json
import io
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import interrupted_controller as controller


class FaultReceiptTests(unittest.TestCase):
    def test_failure_before_injection_records_cleanup_not_injection(self):
        with tempfile.TemporaryDirectory() as d:
            p = controller.FaultProgress(Path(d))
            updater = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            try:
                with self.assertRaises(controller.Refusal) as failure:
                    controller.execute_fault(io.BytesIO(), updater, 1, 0, [], Path(d), Path(d),
                                             Path(d) / 'beacon', port=0, tracked=[], progress=p)
                controller.write_failure_receipt(Path(d), p, failure.exception)
                receipt = json.loads((Path(d) / 'interrupted-fault-failure.json').read_text())
                self.assertIs(receipt['injected'], False)
                self.assertEqual(receipt['cleanup'], 'confirmed')
            finally:
                if updater.poll() is None:
                    updater.kill()
                    updater.wait()

    def test_failure_receipt_preserves_confirmed_injection(self):
        with tempfile.TemporaryDirectory() as d:
            progress = controller.FaultProgress(Path(d))
            progress.controlled('attempted')
            progress.controlled('confirmed')
            controller.write_failure_receipt(Path(d), progress, RuntimeError('recovery'))
            receipt = json.loads((Path(d) / 'interrupted-fault-failure.json').read_text())
            self.assertTrue(receipt['injected'])
            self.assertEqual(receipt['controlled_injection'], 'confirmed')

    def test_progress_boundaries_and_persistence_errors(self):
        with tempfile.TemporaryDirectory() as d:
            p = controller.FaultProgress(Path(d))
            self.assertIs(p.snapshot()['injected'], False)
            with patch.object(p, 'persist', side_effect=OSError('disk')):
                with self.assertRaises(OSError):
                    p.controlled('attempted')
            self.assertIs(p.snapshot()['injected'], False)
            p.controlled('attempted')
            self.assertIsNone(json.loads((Path(d) / 'interrupted-fault-progress.json').read_text())['injected'])
            with patch.object(p, 'persist', side_effect=OSError('disk')):
                with self.assertRaises(OSError):
                    p.controlled('confirmed')
            self.assertIs(p.snapshot()['injected'], True)
            controller.write_failure_receipt(Path(d), p, RuntimeError('accounting'))
            self.assertIs(json.loads((Path(d) / 'interrupted-fault-failure.json').read_text())['injected'], True)

