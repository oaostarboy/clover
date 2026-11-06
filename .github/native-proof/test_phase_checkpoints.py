import json
import pathlib
import subprocess
import tempfile
import unittest

HELPER = pathlib.Path(__file__).with_name('phase-checkpoints.ps1')

class PhaseCheckpoints(unittest.TestCase):
    def test_failure_is_durable_and_redacted(self):
        self.assertTrue(HELPER.exists(), 'Durable phase helper is missing')
        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
$ErrorActionPreference = 'Stop'
. '{HELPER}'
Initialize-NativePhaseJournal '{tmp}' ('a' * 40)
Write-NativePhase 'update-chain' 'started'
try {{ throw 'SECRET_SENTINEL' }} catch {{ Write-NativePhaseFailure; }}
"""
            p = subprocess.run(['pwsh', '-NoProfile', '-Command', script], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            files = sorted(pathlib.Path(tmp).glob('phase-*.json'))
            records = [json.loads(x.read_text()) for x in files]
            self.assertEqual([r['state'] for r in records], ['started', 'failed'])
            self.assertEqual(records[-1]['phase'], 'update-chain')
            self.assertNotIn('SECRET_SENTINEL', p.stdout + ''.join(x.read_text() for x in files))
            self.assertGreaterEqual(records[-1]['elapsed_ms'], records[0]['elapsed_ms'])
            self.assertFalse(list(pathlib.Path(tmp).glob('*.tmp')))

    def test_driver_trap_preserves_original_error(self):
        source = HELPER.with_name('windows.ps1').read_text()
        trap = next(line for line in source.splitlines() if line.startswith('trap {'))
        with tempfile.TemporaryDirectory() as tmp:
            script = f"$ErrorActionPreference='Stop'\n. '{HELPER}'\nInitialize-NativePhaseJournal '{tmp}' ('a'*40)\n{trap}\nWrite-NativePhase 'probe' 'started'\nthrow 'ORIGINAL_FAILURE_SENTINEL'\nWrite-NativePhase 'probe' 'completed'"
            p = subprocess.run(['pwsh', '-NoProfile', '-Command', script], capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn('ORIGINAL_FAILURE_SENTINEL', p.stderr)
            records = [json.loads(x.read_text()) for x in sorted(pathlib.Path(tmp).glob('phase-*.json'))]
            self.assertEqual([r['state'] for r in records], ['started', 'failed'])
            self.assertNotIn('ORIGINAL_FAILURE_SENTINEL', json.dumps(records) + p.stdout)

    def test_driver_brackets_required_witnesses(self):
        source = HELPER.with_name('windows.ps1').read_text()
        for phase in ('installer', 'installer-readiness', 'update-trigger', 'update-chain',
                      'gateway-python', 'stability', 'task-export', 'receipt', 'post-chat', 'preservation'):
            self.assertIn(f"Write-NativePhase '{phase}' 'started'", source, phase)
            self.assertIn(f"Write-NativePhase '{phase}' 'completed'", source, phase)
        self.assertIn('trap { $phaseError = $_; Write-NativePhaseFailure; throw $phaseError }', source)

if __name__ == '__main__':
    unittest.main()
