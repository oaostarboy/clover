import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('native_proof', Path(__file__).with_name('proof.py'))
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)

class Timing(unittest.TestCase):
    def test_saved_observations_include_final_measured_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            output = str(Path(temp) / 'state.json')
            args = SimpleNamespace(previous=None, repo='', home='', sha='fixture', port=0, seconds=.04, output=output)
            state = {'pid':42, 'start_time':123, 'code_sha':'fixture'}
            with patch.object(proof, 'observe_gateway', return_value=state):
                proof.gateway_window(args)
            timed = json.loads(Path(output + '.timed-window.json').read_text())
            self.assertEqual(timed[0]['elapsed_seconds'], 0)
            self.assertGreaterEqual(timed[-1]['elapsed_seconds'], .04)
            self.assertGreaterEqual(len(timed), 2)
            self.assertTrue(all(row['observed_at_ns'] > 0 for row in timed))
            self.assertEqual(json.loads(Path(output).read_text()), state)
            self.assertTrue(all(row == state for row in json.loads(Path(output + '.window.json').read_text())))

    def test_final_boundary_identity_change_refuses(self):
        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(previous=None, repo='', home='', sha='fixture', port=0, seconds=.04, output=str(Path(temp)/'state.json'))
            state = {'pid':42, 'start_time':123, 'code_sha':'fixture'}
            with patch.object(proof, 'observe_gateway', side_effect=[state, dict(state,pid=43)]):
                with self.assertRaisesRegex(ValueError,'changed during stability'):
                    proof.gateway_window(args)

if __name__ == '__main__':
    unittest.main()
