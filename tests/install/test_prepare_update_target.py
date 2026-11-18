"""Real Git regression coverage for same-release install/update CI."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/sandbox/prepare_update_target.py'

class UpdateTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo/'app.txt').write_text('production content\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'released base')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('tag', 'v-test')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True).strip()

    def prepare(self):
        self.assertTrue(SCRIPT.exists(), 'same-release target preparer is missing')
        spec = importlib.util.spec_from_file_location('prepare_update_target', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.prepare_target(self.repo, 'v-test')

    def test_equal_release_gets_marker_only_child_without_moving_tag(self):
        target = self.prepare()
        self.assertNotEqual(target, self.base)
        self.assertEqual(self.git('rev-parse', 'HEAD'), target)
        self.assertEqual(self.git('rev-parse', 'HEAD^'), self.base)
        self.assertEqual(self.git('rev-parse', 'v-test'), self.base)
        self.assertEqual(self.git('diff', '--name-only', self.base, target), '.github/install-e2e/NEXT_MARKER')
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.prepare(), target)

    def test_different_candidate_is_not_modified(self):
        (self.repo/'app.txt').write_text('new production content\n')
        self.git('commit', '-qam', 'candidate')
        candidate = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.prepare(), candidate)
        self.assertFalse((self.repo/'.github/install-e2e/NEXT_MARKER').exists())

    def test_dirty_source_is_refused_without_mutation(self):
        (self.repo/'app.txt').write_text('uncommitted work\n')
        with self.assertRaisesRegex(RuntimeError, 'clean'):
            self.prepare()
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.base)
        self.assertEqual((self.repo/'app.txt').read_text(), 'uncommitted work\n')

if __name__ == '__main__':
    unittest.main()
