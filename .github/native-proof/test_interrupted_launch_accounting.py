import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import interrupted_launch_accounting as m


class AccountingTests(unittest.TestCase):
    def test_empty_trace_requires_bound_thread_chain(self):
        with tempfile.TemporaryDirectory() as d:
            prefix = Path(d) / 'trace'
            Path(str(prefix) + '.10').write_text('1.0 execve("/bin/python", ["gateway"], 0x0) = 0\n2.0 clone3({flags=CLONE_THREAD|CLONE_VM}, 88) = 11\n')
            Path(str(prefix) + '.11').write_text('')
            with self.assertRaises(m.Refusal):
                m.read_process_tree(prefix)
            result = m.read_process_tree(prefix, successor_root=10)
            self.assertEqual(result['empty_thread_tids'], [11])
            self.assertEqual(result['empty_thread_edges'], [{'parent': 10, 'child': 11}])

    def test_empty_trace_refuses_untrusted_edges_and_missing_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'trace'
            def put(pid, text): Path(str(p) + '.' + str(pid)).write_text(text)
            execute = '1.0 execve("/bin/python", ["gateway"], 0x0) = 0\n'
            put(10, execute + '2.0 clone3({flags=CLONE_THREAD|CLONE_VM}, 88) = 11\n')
            put(11, '')
            for root in (None, 11, 12):
                with self.subTest(root=root), self.assertRaises(m.Refusal):
                    m.read_process_tree(p, successor_root=root)
            put(10, execute + '2.0 fork() = 11\n')
            with self.assertRaises(m.Refusal): m.read_process_tree(p, successor_root=10)
            put(10, execute + '2.0 clone3({flags=CLONE_THREAD}, 88) = 11\n3.0 clone3({flags=CLONE_THREAD}, 88) = 11\n')
            with self.assertRaises(m.Refusal): m.read_process_tree(p, successor_root=10)
            put(10, execute + '2.0 clone3({flags=CLONE_THREAD}, 88) = 11\n')
            Path(str(p) + '.11').unlink()
            with self.assertRaises(m.Refusal): m.read_process_tree(p, successor_root=10)
            put(11, '')
            put(12, execute)
            with self.assertRaises(m.Refusal): m.read_process_tree(p, successor_root=10)

    def test_empty_thread_cannot_escape_successor_scope(self):
        cases = [
            # Empty task belongs to the watcher, not the successor.
            {10: '1.0 fork() = 20\n2.0 clone3({flags=CLONE_THREAD}, 88) = 11\n',
             20: '3.0 execve("/gateway", ["gateway"], 0x0) = 0\n', 11: ''},
            # A process below the successor cannot borrow the thread exemption.
            {10: '1.0 fork() = 20\n',
             20: '2.0 execve("/gateway", ["gateway"], 0x0) = 0\n3.0 fork() = 21\n',
             21: '4.0 clone3({flags=CLONE_THREAD}, 88) = 22\n', 22: ''},
        ]
        for records in cases:
            with self.subTest(records=records), tempfile.TemporaryDirectory() as d:
                prefix = Path(d) / 'trace'
                for pid, text in records.items():
                    Path(str(prefix) + '.' + str(pid)).write_text(text)
                with self.assertRaises(m.Refusal):
                    m.read_process_tree(prefix, successor_root=20)

    def test_watcher_thread_requires_terminal_record(self):
        tree = {'root': 10, 'parents': {11: 10, 12: 10, 13: 12}, 'events': [
            {'pid': 10, 'kind': 'fork', 'child': 11, 'thread': False},
            {'pid': 10, 'kind': 'fork', 'child': 12, 'thread': True},
            {'pid': 12, 'kind': 'fork', 'child': 13, 'thread': True},
            {'pid': 11, 'kind': 'exec', 'argv': ['gateway']},
            {'pid': 12, 'kind': 'exit'}, {'pid': 13, 'kind': 'exit'}]}
        good = m.verify_single_recovery_launch(tree, [10], 11, ['gateway'])
        self.assertEqual(good['accepted_threads'], [{'tid': 12, 'owner': 10}, {'tid': 13, 'owner': 10}])
        for tid in (12, 13):
            with self.subTest(tid=tid):
                broken = {**tree, 'events': [e for e in tree['events']
                                          if not (e['pid'] == tid and e['kind'] == 'exit')]}
                with self.assertRaises(m.Refusal):
                    m.verify_single_recovery_launch(broken, [10], 11, ['gateway'])

    def test_signal_syscalls_are_diagnostic_and_strict(self):
        with tempfile.TemporaryDirectory() as d:
            prefix = Path(d) / 'trace'
            path = Path(str(prefix) + '.10')
            path.write_text('1.0 kill(4808, 0) = 0\n2.0 tgkill(5291, 5291, SIGURG) = 0\n3.0 tkill(42, SIGTERM) = -1 ESRCH (No such process)\n')
            events = m.read_process_tree(prefix)['events']
            self.assertEqual([e['kind'] for e in events], ['signal'] * 3)
            for line in ('kill(foo, 0) = 0', 'tgkill(1, 2, 99) = 0', 'kill(1, SIGTERM) = -1 BOGUS (bad)', 'tkill(1, SIGTERM) = 3'):
                path.write_text('1.0 ' + line + '\n')
                with self.subTest(line=line), self.assertRaises(m.Refusal):
                    m.read_process_tree(prefix)

    def test_real_kill_and_tgkill_traces(self):
        with tempfile.TemporaryDirectory() as d:
            prefix = Path(d) / 'trace'
            program = ('import ctypes,os; c=ctypes.CDLL(None); p=os.getpid(); '
                       'os.kill(p,0); assert c.tgkill(p,p,0) == 0')
            subprocess.run([os.environ['RECOVERY_STRACE'], '-ff', '-ttt', '-s', '65536',
                            '-e', 'trace=process', '-o', str(prefix), '--', sys.executable,
                            '-I', '-S', '-c', program], check=True, stderr=subprocess.PIPE, timeout=10)
            events = m.read_process_tree(prefix)['events']
            self.assertEqual({e['syscall'] for e in events if e['kind'] == 'signal'}, {'kill', 'tgkill'})

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

    def test_real_threaded_watcher_and_exact_utility(self):
        with tempfile.TemporaryDirectory() as d:
            prefix = Path(d) / 'trace'
            utility = 'import threading; t=threading.Thread(target=lambda: None); t.start(); t.join()'
            program = ('import threading,subprocess,sys; '
                       't=threading.Thread(target=lambda: None); t.start(); t.join(); '
                       f'subprocess.run([sys.executable,"-I","-S","-c",{utility!r}],check=True); '
                       'subprocess.run(["/usr/bin/true"],check=True)')
            subprocess.run([os.environ['RECOVERY_STRACE'], '-ff', '-ttt', '-s', '65536',
                            '-e', 'trace=process', '-o', str(prefix), '--', sys.executable,
                            '-I', '-S', '-c', program], check=True, stderr=subprocess.PIPE, timeout=10)
            tree = m.read_process_tree(prefix)
            gateway = next(e for e in tree['events'] if e['kind'] == 'exec' and e['argv'] == ['/usr/bin/true'])
            rule = {'parent_pid': tree['root'], 'path': sys.executable,
                    'argv': [sys.executable, '-I', '-S', '-c', utility], 'max_count': 1}
            result = m.verify_single_recovery_launch(tree, [tree['root']], gateway['pid'],
                                                     ['/usr/bin/true'], utilities=[rule])
            self.assertEqual(len(result['accepted_threads']), 2)
            self.assertEqual({x['owner'] for x in result['accepted_threads']},
                             {tree['root'], result['utility_pids'][0]})

    def test_real_unapproved_fork_and_utility_thread_process_refuse(self):
        for utility, extra in [
            ('pass', 'import os; p=os.fork(); os._exit(0) if p==0 else os.waitpid(p,0)'),
            ('import threading,subprocess; t=threading.Thread(target=lambda: subprocess.run(["/usr/bin/true"])); t.start(); t.join()', 'pass')]:
            with self.subTest(utility=utility), tempfile.TemporaryDirectory() as d:
                prefix = Path(d) / 'trace'
                program = (f'import subprocess,sys; {extra}; '
                           f'subprocess.run([sys.executable,"-I","-S","-c",{utility!r}],check=True); '
                           'subprocess.run(["/usr/bin/true"],check=True)')
                subprocess.run([os.environ['RECOVERY_STRACE'], '-ff', '-ttt', '-s', '65536',
                                '-e', 'trace=process', '-o', str(prefix), '--', sys.executable,
                                '-I', '-S', '-c', program], check=True, stderr=subprocess.PIPE, timeout=10)
                tree = m.read_process_tree(prefix)
                gateway = [e for e in tree['events'] if e['kind'] == 'exec' and e['argv'] == ['/usr/bin/true']][-1]
                rule = {'parent_pid': tree['root'], 'path': sys.executable,
                        'argv': [sys.executable, '-I', '-S', '-c', utility], 'max_count': 1}
                with self.assertRaises(m.Refusal):
                    m.verify_single_recovery_launch(tree, [tree['root']], gateway['pid'],
                                                    ['/usr/bin/true'], utilities=[rule])

    def test_thread_exec_refuses(self):
        tree = {'root': 10, 'parents': {11: 10, 12: 10}, 'events': [
            {'pid': 10, 'ns': 1, 'kind': 'fork', 'child': 12, 'thread': True},
            {'pid': 12, 'ns': 2, 'kind': 'exec', 'path': '/bin/true', 'argv': ['true']},
            {'pid': 12, 'ns': 3, 'kind': 'exit'},
            {'pid': 11, 'ns': 4, 'kind': 'exec', 'path': '/bin/gateway', 'argv': ['gateway']}]}
        with self.assertRaises(m.Refusal):
            m.verify_single_recovery_launch(tree, [10], 11, ['gateway'])

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
