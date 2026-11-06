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
    def test_preflight_manifest_exists_before_launch_and_survives_failure(self):
        with tempfile.TemporaryDirectory() as d:
            evidence = Path(d)
            pins = {'source': {'hash': 'abc', 'details': [1, 2]}}
            helpers = {'helper': {'hash': 'def'}}
            expected = {'schema_version': 1, 'stage': 'pre_launch', 'product_commit': 'commit',
                        'product_tree': 'tree', 'source_and_tool_pins': pins,
                        'helper_pins': helpers, 'tracee_argv': ['runtime', '--target', 'fixture']}
            path = evidence / 'interrupted-preflight-pins.json'
            def launch():
                self.assertEqual(json.loads(path.read_text()), expected)
                raise RuntimeError('later recovery failure')
            with self.assertRaisesRegex(RuntimeError, 'later recovery failure'):
                controller.persist_pins_then_launch(evidence, 'commit', 'tree', pins, helpers,
                                                    expected['tracee_argv'], launch)
            self.assertEqual(json.loads(path.read_text()), expected)

    def test_preflight_persistence_failure_never_launches(self):
        with tempfile.TemporaryDirectory() as d:
            launched = []
            with patch.object(controller, 'atomic_receipt', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    controller.persist_pins_then_launch(Path(d), 'commit', 'tree', {}, {}, [],
                                                        lambda: launched.append(True))
            self.assertEqual(launched, [])

    def test_failure_before_injection_records_cleanup_not_injection(self):
        with tempfile.TemporaryDirectory() as d:
            p = controller.FaultProgress(Path(d))
            updater = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            try:
                with self.assertRaises(controller.Refusal) as failure:
                    controller.execute_fault(io.BytesIO(), updater, 1, 0, [], Path(d), Path(d),
                                             Path(d) / 'beacon', old_pidfd=-1, port=0, tracked=[], progress=p)
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

