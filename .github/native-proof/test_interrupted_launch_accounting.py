import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import interrupted_launch_accounting as m


class AccountingTests(unittest.TestCase):
    def test_incomplete_evidence_refuses(self):
        from interrupted_identity import Refusal
        cases = [
            {'10': '1.000001 fork() = 11\n'},
            {'10': '1.000001 execve("/bin/true", ["true", ...], 0x1 /* 0 vars */) = 0\n'},
            {'10': '1.000001 fork( <unfinished ...>\n'},
            {'10': '1.000001 mystery() = 0\n'},
            {'10': '1.000001 exit_group(0) = ?'},
        ]
        for contents in cases:
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as d:
                prefix = Path(d) / 'trace'
                for pid, text in contents.items():
                    Path(str(prefix) + '.' + pid).write_text(text)
                with self.assertRaises(Refusal):
                    m.read_process_tree(prefix)

    def test_fork_without_exec_is_not_silently_ignored(self):
        tree = {'root': 10, 'parents': {11: 10, 12: 10}, 'events': [
            {'pid': 11, 'ns': 1, 'kind': 'exec', 'argv': ['gateway']},
            {'pid': 12, 'ns': 2, 'kind': 'exit'}]}
        with self.assertRaises(m.Refusal):
            m.verify_single_recovery_launch(tree, [10], 11, ['gateway'])

    def test_exact_watcher_utility_is_accounted_separately(self):
        tree = {'root': 10, 'parents': {11: 10, 12: 10}, 'events': [
            {'pid': 11, 'ns': 1, 'kind': 'exec', 'path': '/venv/python', 'argv': ['gateway']},
            {'pid': 12, 'ns': 2, 'kind': 'exec', 'path': '/usr/bin/git', 'argv': ['git', 'rev-parse', 'HEAD']},
            {'pid': 12, 'ns': 3, 'kind': 'exit'}]}
        rules = [{'parent_pid': 10, 'path': '/usr/bin/git',
                  'argv': ['git', 'rev-parse', 'HEAD'], 'max_count': 2}]
        result = m.verify_single_recovery_launch(tree, [10], 11, ['gateway'], utilities=rules)
        self.assertEqual(result['launch_count'], 1)
        self.assertEqual(result['utility_pids'], [12])
        for field, value in [('path', '/tmp/git'), ('argv', ['git', 'reset', '--hard'])]:
            with self.subTest(field=field):
                import copy
                changed = copy.deepcopy(tree)
                changed['events'][1][field] = value
                with self.assertRaises(m.Refusal):
                    m.verify_single_recovery_launch(changed, [10], 11, ['gateway'], utilities=rules)
        tree['parents'][13] = 12
        tree['events'].append({'pid': 13, 'ns': 4, 'kind': 'exec', 'path': '/venv/python', 'argv': ['gateway']})
        with self.assertRaises(m.Refusal):
            m.verify_single_recovery_launch(tree, [10], 11, ['gateway'], utilities=rules)

    def test_utility_limits_and_ambiguous_rules_refuse(self):
        tree = {'root': 10, 'parents': {11: 10, 12: 10, 13: 10}, 'events': [
            {'pid': 11, 'ns': 1, 'kind': 'exec', 'path': '/gateway', 'argv': ['gateway']},
            {'pid': 12, 'ns': 2, 'kind': 'exec', 'path': '/usr/bin/git', 'argv': ['git', 'rev-parse', 'HEAD']},
            {'pid': 12, 'ns': 3, 'kind': 'exit'},
            {'pid': 13, 'ns': 4, 'kind': 'exec', 'path': '/usr/bin/git', 'argv': ['git', 'rev-parse', 'HEAD']},
            {'pid': 13, 'ns': 5, 'kind': 'exit'}]}
        rule = {'parent_pid': 10, 'path': '/usr/bin/git',
                'argv': ['git', 'rev-parse', 'HEAD'], 'max_count': 1}
        with self.assertRaisesRegex(m.Refusal, 'count exceeded'):
            m.verify_single_recovery_launch(tree, [10], 11, ['gateway'], utilities=[rule])
        rule['max_count'] = 2
        with self.assertRaisesRegex(m.Refusal, 'ambiguously'):
            m.verify_single_recovery_launch(tree, [10], 11, ['gateway'], utilities=[rule, rule])

    def test_real_git_head_query_is_exactly_accounted(self):
        import shutil
        git = shutil.which('git')
        if git is None:
            self.fail('git is required for the real utility regression')
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([git, 'init', '-q', d], check=True)
            subprocess.run([git, '-C', d, '-c', 'user.name=Fixture', '-c',
                            'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture'], check=True)
            prefix = Path(d) / 'trace'
            program = ('import subprocess; subprocess.run(["git", "rev-parse", "HEAD"], check=True); '
                       'subprocess.run(["/usr/bin/true"], check=True)')
            subprocess.run([os.environ['RECOVERY_STRACE'], '-ff', '-ttt', '-s', '65536',
                            '-e', 'trace=process', '-o', str(prefix), '--', sys.executable,
                            '-I', '-S', '-c', program], cwd=d, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
            tree = m.read_process_tree(prefix)
            launches = [e for e in tree['events'] if e['kind'] == 'exec' and e['argv'] == ['/usr/bin/true']]
            self.assertEqual(len(launches), 1)
            rule = {'parent_pid': tree['root'], 'path': git,
                    'argv': ['git', 'rev-parse', 'HEAD'], 'max_count': 1}
            result = m.verify_single_recovery_launch(tree, [tree['root']], launches[0]['pid'],
                                                     ['/usr/bin/true'], utilities=[rule])
            self.assertEqual(result['utility_counts'], [1])
            for change in ({'parent_pid': launches[0]['pid']}, {'path': '/wrong/git'},
                           {'argv': ['git', 'rev-parse', 'other']}, {'max_count': 0}):
                with self.subTest(change=change), self.assertRaises(m.Refusal):
                    m.verify_single_recovery_launch(tree, [tree['root']], launches[0]['pid'],
                        ['/usr/bin/true'], utilities=[{**rule, **change}])

    def test_real_duplicate_short_lived_launches_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            prefix = Path(d) / 'trace'
            subprocess.run([os.environ['RECOVERY_STRACE'], '-ff', '-ttt', '-s', '65536',
                '-e', 'trace=process', '-o', str(prefix), '--', sys.executable,
                '-I', '-S', '-c',
                'import subprocess; subprocess.run(["/usr/bin/true"]); subprocess.run(["/usr/bin/true"])'],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
            tree = m.read_process_tree(prefix)
            launches = [e for e in tree['events'] if e['kind'] == 'exec' and e['argv'] == ['/usr/bin/true']]
            self.assertEqual(len(launches), 2)
            with self.assertRaises(m.Refusal):
                m.verify_single_recovery_launch(tree, [tree['root']],
                                               launches[0]['pid'], ['/usr/bin/true'])

    def test_reads_real_fork_and_short_lived_exec(self):
        with tempfile.TemporaryDirectory() as d:
            prefix = Path(d) / 'trace'
            subprocess.run([os.environ['RECOVERY_STRACE'], '-ff', '-ttt', '-s', '65536',
                '-e', 'trace=process', '-o', str(prefix), '--', sys.executable,
                '-I', '-S', '-c',
                'import os; p=os.fork(); os.execl("/usr/bin/true", "true") if p==0 else os.waitpid(p,0)'],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
            tree = m.read_process_tree(prefix)
            launches = [e for e in tree['events'] if e['kind'] == 'exec']
            self.assertEqual(len(launches), 2)
            child = [e for e in launches if e['argv'] == ['true']]
            self.assertEqual(len(child), 1)
            self.assertNotEqual(child[0]['pid'], tree['root'])
            self.assertEqual(tree['parents'][child[0]['pid']], tree['root'])
            result = m.verify_single_recovery_launch(tree, [tree['root']], child[0]['pid'], ['true'])
            self.assertEqual(result['gateway_pid'], child[0]['pid'])
            self.assertEqual(result['launch_count'], 1)


if __name__ == '__main__':
    unittest.main()
