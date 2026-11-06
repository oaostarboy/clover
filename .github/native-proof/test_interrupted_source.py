import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

import interrupted_source as source


class SourceTests(unittest.TestCase):
    def test_rejects_dirty_bytes_and_wrong_commit(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            def git(*args):
                return subprocess.run(['git', '-C', d, *args], check=True, capture_output=True, text=True).stdout.strip()
            git('init', '-q'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid')
            (p / 'module.py').write_text('value = 1\n')
            git('add', 'module.py'); git('commit', '-qm', 'initial')
            commit = git('rev-parse', 'HEAD')
            tree = git('rev-parse', 'HEAD^{tree}')
            self.assertEqual(source.pinned_hash(p, commit, tree, 'module.py'), hashlib.sha256((p / 'module.py').read_bytes()).hexdigest())
            with self.assertRaises(source.Refusal): source.pinned_hash(p, '0' * 40, tree, 'module.py')
            (p / 'module.py').write_text('value = 2\n')
            with self.assertRaises(source.Refusal): source.pinned_hash(p, commit, tree, 'module.py')

if __name__ == '__main__': unittest.main()
