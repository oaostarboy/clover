"""Installer-only mode must stop after the real identity probe."""
import subprocess
from pathlib import Path
from test_posix_npm_evidence_contract import SCRIPT, fixture


def test_install_only_cannot_enter_gateway_or_controller(tmp_path):
    env, runner = fixture(tmp_path)
    env['NATIVE_INSTALL_ONLY'] = '1'
    Path(env['FIXTURE_INSTALLER']).write_text('''#!/usr/bin/env bash
set -eu
python - <<'PY'
import os, pathlib, sys
p = pathlib.Path(os.environ['CLOVER_INSTALL_DIR']) / 'venv/bin/python'
p.parent.mkdir(parents=True)
p.symlink_to(sys.executable)
PY
''')
    proof = Path(env['GITHUB_WORKSPACE']) / 'helpers/.github/native-proof/proof.py'
    proof.write_text('''import pathlib, sys
if sys.argv[1] == "identity":
    pathlib.Path("IDENTITY_RAN").touch()
''')
    result = subprocess.run(['bash', str(SCRIPT), 'old2new'], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stderr
    assert (runner / 'neutral/IDENTITY_RAN').exists()
    assert not (runner / 'evidence/gateway-launch.log').exists()
    assert not (Path(env['HOME']) / '.clover/.env').exists()
