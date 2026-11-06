import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import interrupted_ownership as own


class Proc:
    pid = 52
    def ppid(self): return 51
    def create_time(self): return 100.0
    def exe(self): return '/usr/bin/python3'
    def cmdline(self): return ['/usr/bin/python3', '/fixture/watcher.py', '/fixture/home/beacon']
    def cwd(self): return '/fixture/repo'
    def environ(self): return {'CLOVER_HOME': '/fixture/home', 'SECRET': 'not-recorded'}
    def uids(self): return SimpleNamespace(real=1000, effective=1000)


class OwnershipTests(unittest.TestCase):
    def bind(self, proc=None, **kwargs):
        values = dict(updater_pid=51, expected_argv=Proc().cmdline(), expected_exe='/usr/bin/python3', home='/fixture/home', cwd='/fixture/repo', uid=1000)
        values.update(kwargs)
        with patch.object(own.identity, 'birth', return_value=500):
            return own.bind_child(proc or Proc(), **values)

    def test_exact_child_binding_retains_birth_without_environment(self):
        result = self.bind()
        self.assertEqual(result['birth_ticks'], 500)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_wrong_parent_home_argv_and_uid_refuse(self):
        for change in [dict(updater_pid=50), dict(home='/other'), dict(expected_argv=['python']), dict(uid=1001)]:
            with self.subTest(change=change), self.assertRaises(own.Refusal):
                self.bind(**change)

    def test_exec_change_during_inspection_refuses(self):
        proc = Proc()
        with patch.object(proc, 'exe', side_effect=['/usr/bin/python3', '/other']), self.assertRaises(own.Refusal):
            self.bind(proc)

    def test_beacon_must_bind_updater_and_private_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'; home.mkdir()
            repo = Path(tmp) / 'repo'; repo.mkdir()
            beacon = home / 'beacon'
            data = dict(updater_pid=51, updater_create_time=200.0, cwd=str(repo), repo=str(repo), gateway_argv=['python', '-m', 'gateway'])
            beacon.write_text(json.dumps(data))
            own.validate_beacon(beacon, home=home, repo=repo, updater_pid=51, updater_create_time=200.0, gateway_argv=data['gateway_argv'])
            for field,value in [('updater_pid', 99), ('updater_create_time', 201.0), ('cwd', '/other'), ('repo', '/other')]:
                beacon.write_text(json.dumps({**data, field:value}))
                with self.subTest(field=field), self.assertRaises(own.Refusal):
                    own.validate_beacon(beacon, home=home, repo=repo, updater_pid=51, updater_create_time=200.0, gateway_argv=data['gateway_argv'])

if __name__ == '__main__':
    unittest.main()
