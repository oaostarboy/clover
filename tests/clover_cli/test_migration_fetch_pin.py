"""Pinning must reject an untrusted fetched tracking ref."""
import subprocess
import pytest


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def repo(path):
    path.mkdir()
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    git(path, 'config', 'user.email', 'test@example.invalid')
    git(path, 'config', 'user.name', 'Test')
    (path / 'pyproject.toml').write_text('fixture')
    (path / 'clover_cli').mkdir()
    (path / 'clover_cli' / '__init__.py').write_text('')
    git(path, 'add', '.')
    git(path, 'commit', '-qm', 'fixture')
    return git(path, 'rev-parse', 'HEAD')


def test_fetched_ref_must_match_stage_identity(tmp_path):
    from clover_cli import _migration_target as b
    stage, target = tmp_path / 'stage', tmp_path / 'target'
    stage_id, target_id = repo(stage), repo(target)
    (stage / 'candidate').write_text('candidate')
    subprocess.run(['git', '-C', str(stage), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(stage), 'commit', '-qm', 'candidate'], check=True)
    stage_id = git(stage, 'rev-parse', 'HEAD')
    with b.bind_target(stage, target, stage_id, target_id):
        subprocess.run(['git', '-C', str(target), 'update-ref', 'refs/remotes/origin/main', target_id], check=True)
        with pytest.raises(ValueError, match='fetched'):
            b.verified_fetch_ref('main')


def test_fetch_identity_validation_disables_optional_git_locks(tmp_path, monkeypatch):
    from clover_cli import _migration_target as b
    stage, target = tmp_path / 'stage', tmp_path / 'target'
    stage_id, target_id = repo(stage), repo(target)
    with b.bind_target(stage, target, stage_id, target_id):
        subprocess.run(['git', '-C', str(target), 'update-ref', 'refs/remotes/origin/main', target_id], check=True)
        real_run = subprocess.run
        environments = []
        def run(*args, **kwargs):
            environments.append(kwargs.get('env'))
            return real_run(*args, **kwargs)
        monkeypatch.setattr(b.subprocess, 'run', run)
        with pytest.raises(ValueError):
            b.verified_fetch_ref('main')
        assert environments and all(env.get('GIT_OPTIONAL_LOCKS') == '0' for env in environments)
