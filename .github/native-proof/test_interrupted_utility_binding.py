"""Source-bound utility policy refuses altered watcher bytes and tools."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import interrupted_utility_binding as binding
from interrupted_identity import Refusal


class UtilityBindingTests(unittest.TestCase):
    def test_helper_pins_detect_changed_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            helper = Path(d) / 'helper.py'
            helper.write_text('original')
            pins = binding.pin_helpers([helper], sys.executable)
            binding.check_helpers(pins)
            helper.write_text('changed')
            with self.assertRaisesRegex(Refusal, 'helper changed'):
                binding.check_helpers(pins)

    def test_pins_frozen_source_and_exact_success_path_tools(self):
        source = Path(__file__).resolve().parents[2] / 'clover_cli/update_restart_watcher.py'
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stage = root / 'stage'; stage.mkdir()
            watcher = stage / 'clover_cli/update_restart_watcher.py'
            watcher.parent.mkdir(); watcher.write_bytes(source.read_bytes())
            git = shutil.which('git'); ps = shutil.which('ps')
            assert git and ps
            subprocess.run([git, 'init', '-q', str(stage)], check=True)
            subprocess.run([git, '-C', str(stage), 'add', '.'], check=True)
            subprocess.run([git, '-C', str(stage), '-c', 'user.name=Fixture', '-c',
                            'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
            commit = subprocess.check_output([git, '-C', str(stage), 'rev-parse', 'HEAD'], text=True).strip()
            tree = subprocess.check_output([git, '-C', str(stage), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
            frozen = root / 'frozen.py'; frozen.write_bytes(source.read_bytes())
            python = Path(os.path.abspath(sys.executable))
            pins = binding.preflight(stage, commit, tree, python, {'PATH': os.environ['PATH']})
            rules = binding.bind_rules(pins, frozen, rollback_pid=42)
            self.assertEqual([r['argv'] for r in rules], [
                ['git', 'rev-parse', 'HEAD'],
                [str(python), '-c', 'import clover_cli.main, gateway.run'],
                ['ps', '-eo', 'args']])
            self.assertEqual([r['max_count'] for r in rules], [128, 128, 256])
            self.assertTrue(all(r['parent_pid'] == 42 for r in rules))
            self.assertEqual(pins['source_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
            original = pins['tools']['git']['sha256']
            pins['tools']['git']['sha256'] = '0' * 64
            with self.assertRaises(Refusal):
                binding.bind_rules(pins, frozen, rollback_pid=42)
            pins['tools']['git']['sha256'] = original
            frozen.write_bytes(b'changed')
            with self.assertRaises(Refusal):
                binding.bind_rules(pins, frozen, rollback_pid=42)
