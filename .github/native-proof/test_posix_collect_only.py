"""Post-step collection must not repeat installation."""
import json
from pathlib import Path
import subprocess
from test_posix_npm_evidence_contract import SCRIPT, fixture


def test_collect_only_recovers_logs_without_invoking_installer(tmp_path):
    env, runner = fixture(tmp_path)
    first = subprocess.run(['bash', str(SCRIPT), 'old2new'], env=env, cwd=tmp_path,
                           capture_output=True, text=True, timeout=35)
    assert first.returncode == 1
    report = runner / 'evidence/install-diagnostics.json'
    report.unlink()
    Path(env['FIXTURE_INSTALLER']).write_text('exit 91\n')
    (runner / 'bin/git').write_text('#!/bin/sh\nexit 92\n')
    env['NATIVE_COLLECT_INSTALL_EVIDENCE_ONLY'] = '1'
    collected = subprocess.run(['bash', str(SCRIPT), 'old2new'], env=env, cwd=tmp_path,
                               capture_output=True, text=True, timeout=35)
    assert collected.returncode == 0, collected.stdout + collected.stderr
    data = json.loads(report.read_text())
    assert data['installer_exit'] is None
    assert data['npm_logs']
    text = '\n'.join(p.read_text() for p in (runner / 'evidence').rglob('*') if p.is_file())
    assert all(secret not in text for secret in ('shortsecret', 'shortpass', 'shorttoken'))
    assert not list(tmp_path.rglob('IDENTITY_MUST_NOT_RUN'))


def test_collect_only_preserves_existing_completed_report(tmp_path):
    env, runner = fixture(tmp_path)
    first = subprocess.run(['bash', str(SCRIPT), 'old2new'], env=env, cwd=tmp_path,
                           capture_output=True, text=True, timeout=35)
    assert first.returncode == 1
    report = runner / 'evidence/install-diagnostics.json'
    original = report.read_bytes()
    env['NATIVE_COLLECT_INSTALL_EVIDENCE_ONLY'] = '1'
    result = subprocess.run(['bash', str(SCRIPT), 'old2new'], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stderr
    assert report.read_bytes() == original
    assert json.loads(original)['installer_exit'] == 1


def test_collect_only_fails_without_before_metadata(tmp_path):
    env, runner = fixture(tmp_path)
    env['NATIVE_COLLECT_INSTALL_EVIDENCE_ONLY'] = '1'
    result = subprocess.run(['bash', str(SCRIPT), 'old2new'], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=35)
    assert result.returncode != 0
    assert not (runner / 'evidence/install-diagnostics.json').exists()
