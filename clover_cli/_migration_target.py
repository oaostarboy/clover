"""Invocation-local validation primitive for a future staged updater.

This module does not start an update or alter normal CLI root resolution.  A
caller must arrange isolated imports and hold update admission before writing.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import threading

_target = None
_identities = None
_prepared_commit = None
_lock = threading.Lock()


def target_root():
    """Return the currently bound installation root, if any."""
    return _target


def revalidate_target():
    """Recheck an active binding after the updater acquires its lock."""
    if _identities is None:
        return
    stage, target, stage_commit, target_commit = _identities
    _physical_root(stage)
    _physical_root(target)
    _verify_git(stage, stage_commit)
    _verify_git(target, target_commit)


def verified_fetch_ref(branch):
    """Return the immutable stage object only if the fetched ref matches it."""
    if _identities is None:
        return None
    if branch != 'main':
        raise ValueError('staged migration only supports main')
    _, target, stage_commit, _ = _identities
    env = {'PATH': os.defpath, 'HOME': str(target / '.git'),
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
           'GIT_NO_REPLACE_OBJECTS': '1', 'LC_ALL': 'C'}
    def git(*args):
        return subprocess.run(['git', '-C', str(target), *args], env=env,
                              text=True, capture_output=True, check=False)
    fetched = git('rev-parse', '--verify', 'refs/remotes/origin/main^{commit}')
    if fetched.returncode or fetched.stdout.strip() != stage_commit:
        raise ValueError('fetched target does not match staged commit')
    current = git('symbolic-ref', '--quiet', '--short', 'HEAD')
    if current.returncode or current.stdout.strip() != branch:
        raise ValueError('staged migration requires checkout on main')
    if git('merge-base', '--is-ancestor', 'HEAD', stage_commit).returncode:
        raise ValueError('staged migration requires a fast-forward')
    return stage_commit


def prepare_staged_fetch(branch):
    """Fetch and pin the selected staged commit under the caller's update lock."""
    global _prepared_commit
    if _identities is None or branch != 'main':
        raise ValueError('staged migration requires a bound target on main')
    target = _identities[1]
    result = subprocess.run(
        ['git', '-C', str(target), 'fetch', 'origin',
         '+refs/heads/main:refs/remotes/origin/main'],
        text=True, capture_output=True, stdin=subprocess.DEVNULL, check=False,
    )
    if result.returncode:
        raise ValueError('staged fetch failed: ' + result.stderr.strip())
    _prepared_commit = verified_fetch_ref(branch)
    return _prepared_commit


def prepared_staged_commit():
    if _identities is None:
        return None
    if _prepared_commit is None:
        raise ValueError('staged source has not been prepared')
    return _prepared_commit


def _physical_root(root):
    path = Path(root)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('root must be an existing absolute directory')
    if Path(os.path.abspath(path)) != path.resolve():
        raise ValueError('root must not traverse symlinks')
    if not (path / '.git').is_dir() or (path / '.git').is_symlink():
        raise ValueError('root must be an owned Git checkout')
    if not (path / 'pyproject.toml').is_file() or not (path / 'clover_cli' / '__init__.py').is_file():
        raise ValueError('missing installation metadata')
    if hasattr(os, 'getuid') and (path.stat().st_uid != os.getuid() or (path / '.git').stat().st_uid != os.getuid()):
        raise ValueError('checkout is not owned by the current user')
    return path


def _verify_git(root, expected):
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', expected):
        raise ValueError('expected commit must be a full lowercase Git object id')
    # Do not inherit Git configuration, replacement refs, or a caller's Git dir.
    env = {'PATH': os.defpath, 'HOME': str(root / '.git'), 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_NO_REPLACE_OBJECTS': '1',
           'GIT_OPTIONAL_LOCKS': '0', 'LC_ALL': 'C'}
    def git(*args):
        result = subprocess.run(['git', '-C', str(root), '-c', 'core.fsmonitor=false', *args],
                                env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise ValueError('cannot verify Git checkout')
        return result.stdout.strip()
    if git('rev-parse', '--show-toplevel') != str(root):
        raise ValueError('not the checkout root')
    if git('rev-parse', 'HEAD') != expected:
        raise ValueError('checkout identity mismatch')
    # A status refresh can invoke checkout-configured clean/process filters.
    # Refuse them before asking Git to inspect the worktree.
    config = git('config', '--local', '--includes', '--list')
    if any(line.lower().startswith('filter.') for line in config.splitlines()):
        raise ValueError('Git filter configuration is unsupported')
    if git('status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('checkout is not clean')


@contextmanager
def bind_target(stage, target, expected_stage_commit, expected_target_commit):
    """Bind one validated target for the duration of a controller invocation.

    Identities must come from a trusted caller, never from the checkout itself.
    Nested or concurrent bindings are rejected, even to the same target.
    """
    global _target, _identities, _prepared_commit
    if not _lock.acquire(blocking=False):
        raise RuntimeError('a migration target is already bound')
    try:
        stage = _physical_root(stage)
        target = _physical_root(target)
        if stage == target or stage in target.parents or target in stage.parents:
            raise ValueError('stage and target must be disjoint')
        _verify_git(stage, expected_stage_commit)
        _verify_git(target, expected_target_commit)
        _target = target
        _identities = (stage, target, expected_stage_commit, expected_target_commit)
        try:
            yield target
        finally:
            _target = None
            _identities = None
            _prepared_commit = None
    finally:
        _lock.release()
