"""Workflow routing contracts; not native installation acceptance."""
from pathlib import Path
import unittest
import subprocess

ROOT = Path(__file__).resolve().parents[2]


class Routing(unittest.TestCase):
    def test_actual_shell_mode_guards(self):
        prefix = (ROOT / '.github/native-proof/posix.sh').read_text().split('\nPROOF=', 1)[0]
        rows = [('old2new', '1', '1', True), ('old2new', '1', '0', True),
                ('new2next', '0', '0', True), ('fresh', '0', '0', True),
                ('fresh', '1', '0', False), ('new2next', '1', '0', False),
                ('old2new', '0', '1', False)]
        for phase, staged, proof, valid in rows:
            with self.subTest(phase=phase, staged=staged, proof=proof):
                env = {'PATH': '/usr/bin:/bin', 'GITHUB_ACTIONS': 'true',
                       'RUNNER_ENVIRONMENT': 'github-hosted',
                       'NATIVE_STAGED_ENTRY': staged, 'NATIVE_STAGE_INDEPENDENCE': proof}
                result = subprocess.run(['bash', '-c', prefix, 'routing', phase],
                                        env=env, capture_output=True, timeout=5)
                self.assertEqual(result.returncode == 0, valid)

    def test_old_to_new_explicitly_selects_staged_entry(self):
        workflow = (ROOT / '.github/workflows/native-exact-proof.yml').read_text()
        posix = workflow.split('\n  posix:\n')[1].split('\n  windows:\n')[0]
        self.assertIn("NATIVE_STAGED_ENTRY: ${{ matrix.phase == 'old2new' && '1' || '0' }}", posix)
        self.assertIn('run-staged-posix.sh', (ROOT / '.github/native-proof/posix.sh').read_text())

    def test_linux_only_origin_observer_is_not_run_on_macos(self):
        workflow = (ROOT / '.github/workflows/native-exact-proof.yml').read_text()
        self.assertIn("NATIVE_STAGE_INDEPENDENCE: ${{ matrix.os == 'ubuntu-latest' && matrix.phase == 'old2new' && '1' || '0' }}", workflow)
        helper = (ROOT / '.github/native-proof/posix.sh').read_text()
        self.assertIn('STAGE_PROOF=${NATIVE_STAGE_INDEPENDENCE:-${NATIVE_STAGED_ENTRY:-0}}', helper)
        self.assertIn('if [[ $STAGE_PROOF == 1 ]]; then', helper)

    def test_normal_update_route_is_preserved(self):
        helper = (ROOT / '.github/native-proof/posix.sh').read_text()
        self.assertIn('if [[ ${NATIVE_STAGED_ENTRY:-0} == 1 ]]; then\n    source', helper)
        self.assertIn('else\n    "$BIN" update --yes --gateway', helper)

    def test_no_staged_entry_for_fresh_or_candidate_marker(self):
        helper = (ROOT / '.github/native-proof/posix.sh').read_text()
        self.assertIn('[[ ${NATIVE_STAGED_ENTRY:-0} != 1 || $MODE == old2new ]]', helper)
        self.assertIn('[[ $STAGE_PROOF != 1 || ${NATIVE_STAGED_ENTRY:-0} == 1 ]]', helper)


if __name__ == '__main__':
    unittest.main()
