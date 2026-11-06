import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('preservation', Path(__file__).with_name('preservation.py'))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

class Preservation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / 'config.yaml').write_text('model: fixture\n_config_version: 41\n')
        with sqlite3.connect(self.home / 'state.db') as db:
            db.executescript('CREATE TABLE sessions(id TEXT, source TEXT); CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT);')
            db.execute('INSERT INTO sessions VALUES (?,?)',(module.SESSION,'native-proof'))
            db.executemany('INSERT INTO messages VALUES (?,?,?,?)',[(1,module.SESSION,'user',module.USER),(2,module.SESSION,'assistant',module.ASSISTANT)])

    def test_preserves_messages_and_all_existing_settings(self):
        before = module.snapshot(self.home)
        (self.home / 'config.yaml').write_text('model: fixture\n_config_version: 42\nnew_default: true\n')
        module.verify(before, module.snapshot(self.home))

    def test_changed_setting_is_refused(self):
        before = module.snapshot(self.home)
        (self.home / 'config.yaml').write_text('model: WRONG\n_config_version: 42\n')
        with self.assertRaisesRegex(ValueError,'config'):
            module.verify(before, module.snapshot(self.home))

    def test_missing_conversation_is_refused(self):
        with sqlite3.connect(self.home / 'state.db') as db:
            db.execute('DELETE FROM messages WHERE id=2')
        with self.assertRaisesRegex(ValueError,'witness'):
            module.snapshot(self.home)

    def test_mutated_message_field_is_refused(self):
        before = module.snapshot(self.home)
        with sqlite3.connect(self.home / 'state.db') as db:
            db.execute('UPDATE messages SET role="system" WHERE id=1')
        with self.assertRaises(ValueError):
            module.verify(before, module.snapshot(self.home))

if __name__ == '__main__':
    unittest.main()
