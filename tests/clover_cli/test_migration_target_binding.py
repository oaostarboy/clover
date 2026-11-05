"""The staged controller must not confuse code and installation roots."""
import os
import subprocess
from pathlib import Path

import pytest


def repo(path):
    path.mkdir()
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    subprocess.run(['git', '-C', str(path), 'config', 'user.email', 'test@example.invalid'], check=True)
    subprocess.run(['git', '-C', str(path), 'config', 'user.name', 'Test'], check=True)
    (path / 'pyproject.toml').write_text('[project]\nname="clover-c1"\nversion="0"\n')
    (path / 'clover_cli').mkdir()
    (path / 'clover_cli' / '__init__.py').write_text('')
    subprocess.run(['git', '-C', str(path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(path), 'commit', '-qm', 'fixture'], check=True)
    return subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()


def test_binding_validates_explicit_identity_and_keeps_code_root(tmp_path):
    from clover_cli import _migration_target as binding
    from clover_cli import _startup_fast
    stage, target = tmp_path / 'stage', tmp_path / 'target'
    stage_id, target_id = repo(stage), repo(target)
    with binding.bind_target(stage, target, stage_id, target_id):
        assert binding.target_root() == target
        assert Path(_startup_fast.project_root_str()) != target
        with pytest.raises(RuntimeError):
            with binding.bind_target(stage, target, stage_id, target_id):
                pass
    assert binding.target_root() is None


def test_binding_refuses_wrong_identity_and_symlinks(tmp_path):
    from clover_cli import _migration_target as binding
    stage, target = tmp_path / 'stage', tmp_path / 'target'
    stage_id, target_id = repo(stage), repo(target)
    with pytest.raises(ValueError):
        with binding.bind_target(stage, target, '0' * 40, target_id):
            pass
    link = tmp_path / 'link'
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        with binding.bind_target(stage, link, stage_id, target_id):
            pass
    assert binding.target_root() is None


def test_binding_refuses_git_clean_filter_without_executing_it(tmp_path):
    from clover_cli import _migration_target as binding
    stage, target = tmp_path / 'stage', tmp_path / 'target'
    stage_id, target_id = repo(stage), repo(target)
    marker = tmp_path / 'executed'
    (target / '.gitattributes').write_text('pyproject.toml filter=evil\n')
    subprocess.run(['git', '-C', str(target), 'add', '.gitattributes'], check=True)
    subprocess.run(['git', '-C', str(target), 'commit', '-qm', 'attributes'], check=True)
    target_id = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True).strip()
    subprocess.run(['git', '-C', str(target), 'config', 'filter.evil.clean', f'touch {marker}; cat'], check=True)
    (target / 'pyproject.toml').write_text('changed')
    with pytest.raises(ValueError, match='Git filter configuration'):
        with binding.bind_target(stage, target, stage_id, target_id):
            pass
    assert not marker.exists()
