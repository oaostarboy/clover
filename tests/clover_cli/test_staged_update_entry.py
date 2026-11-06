"""Staged updater refuses unsafe startup before touching target."""
from pathlib import Path
import subprocess
import sys
import pytest


def test_entry_refuses_startup_with_site_enabled(tmp_path):
    root = Path(__file__).resolve().parents[2]
    entry = root / 'scripts' / 'staged_update.py'
    p = subprocess.run([sys.executable, '-I', str(entry)], text=True, capture_output=True)
    assert p.returncode != 0
    assert 'requires -I -S' in p.stderr


@pytest.mark.parametrize('nested_install', [False, True])
def test_isolated_entry_pins_stage_before_importing_target(tmp_path, nested_install):
    """Fresh-process bootstrap contract; the fake CLI is only a witness."""
    import shutil
    source = Path(__file__).resolve().parents[2]
    stage = tmp_path / 'stage'
    (stage / 'scripts').mkdir(parents=True)
    (stage / 'clover_cli').mkdir()
    shutil.copy2(source / 'scripts/staged_update.py', stage / 'scripts/staged_update.py')
    shutil.copy2(source / 'clover_cli/_migration_target.py', stage / 'clover_cli/_migration_target.py')
    (stage / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="0"\n')
    (stage / 'clover_cli/__init__.py').write_text('')
    (stage / 'clover_cli/main.py').write_text(
        'from clover_cli._migration_target import target_root\n'
        'def cmd_update(args):\n'
        '    assert args.branch == "main" and args.yes\n'
        '    print("STAGED_TARGET=" + str(target_root()))\n')
    def git(root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    subprocess.run(['git', 'init', '-qb', 'main', str(stage)], check=True)
    git(stage, 'config', 'user.name', 'Fixture')
    git(stage, 'config', 'user.email', 'fixture@example.invalid')
    git(stage, 'add', '.')
    git(stage, 'commit', '-qm', 'stage')
    commit, tree = git(stage, 'rev-parse', 'HEAD'), git(stage, 'rev-parse', 'HEAD^{tree}')
    home = tmp_path / 'profile'
    home.mkdir()
    target = home / 'clover-c1' if nested_install else tmp_path / 'target'
    subprocess.run(['git', 'clone', '-q', str(stage), str(target)], check=True)
    marker = tmp_path / 'old-code-executed'
    (target / 'clover_cli/__init__.py').write_text(f'open({str(marker)!r}, "w").write("bad")\n')
    git(target, 'config', 'user.name', 'Fixture')
    git(target, 'config', 'user.email', 'fixture@example.invalid')
    git(target, 'add', '.')
    git(target, 'commit', '-qm', 'hostile target module')
    old = git(target, 'rev-parse', 'HEAD')
    (target / '.git/info/exclude').write_text('venv/\n')
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(target / 'venv')], check=True)
    args = [sys.executable, '-I', '-S', str(stage / 'scripts/staged_update.py'),
            '--target', str(target), '--old-commit', old,
            '--stage-commit', commit, '--stage-tree', tree, '--home', str(home)]
    result = subprocess.run(args, cwd=target, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert f'STAGED_TARGET={target}' in result.stdout
    assert not marker.exists()
    wrong = args.copy()
    wrong[wrong.index('--stage-tree') + 1] = '0' * 40
    refused = subprocess.run(wrong, cwd=target, text=True, capture_output=True)
    assert refused.returncode == 2
    assert 'stage tree' in refused.stderr
    assert not marker.exists()
