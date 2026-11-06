"""Invocation-local validation primitive for a future staged updater.

This module does not start an update or alter normal CLI root resolution.  A
caller must arrange isolated imports and hold update admission before writing.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import sys
import threading

_target = None
_identities = None
_prepared_commit = None
_trusted_dependencies = ()
_lock = threading.Lock()


def target_root():
    """Return the currently bound installation root, if any."""
    return _target


def trusted_dependency_paths():
    """Revalidate the explicit caller-trusted directories, never site/sys.path."""
    if _target is None:
        return ()
    if not _trusted_dependencies:
        raise ValueError('trusted watcher dependencies were not bound')
    for path, identity in _trusted_dependencies:
        if not path.is_dir() or path.resolve() != path:
            raise ValueError('trusted watcher dependency directory changed')
        stat = path.stat()
        if (stat.st_dev, stat.st_ino) != identity:
            raise ValueError('trusted watcher dependency identity changed')
        if _identities and any(path == root or root in path.parents or path in root.parents
                               for root in _identities[:2]):
            raise ValueError('trusted watcher dependencies overlap source')
    return tuple(path for path, _ in _trusted_dependencies)


def frozen_watcher_command(script, beacon):
    """Preflight psutil in isolated startup before returning a frozen command."""
    if _target is None:
        return [sys.executable, str(script), str(beacon)]
    dependencies = [str(path) for path in trusted_dependency_paths()]
    bootstrap = (
        'import sys,runpy; from pathlib import Path; '
        f'roots={dependencies!r}; sys.path[:0]=roots; '
        'import psutil; '
        'assert any(Path(psutil.__file__).resolve().is_relative_to(Path(p)) for p in roots), "untrusted psutil"; '
        'assert psutil.Process().create_time() > 0; '
        'check=sys.argv[-1] == "--check-trusted-watcher"; '
        'sys.argv=sys.argv[1:]; '
        'print("trusted-watcher-ready") if check else runpy.run_path(sys.argv[0], run_name="__main__")'
    )
    command = [sys.executable, '-I', '-S', '-c', bootstrap, str(script), str(beacon)]
    env = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON') and k != 'VIRTUAL_ENV'}
    try:
        result = subprocess.run(command + ['--check-trusted-watcher'], env=env,
                                capture_output=True, text=True, timeout=15,
                                stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('trusted watcher dependency preflight failed') from exc
    if result.returncode or result.stdout.strip() != 'trusted-watcher-ready':
        raise ValueError('trusted watcher dependency preflight failed')
    return command


def target_python():
    """Return the bound target's lexical venv interpreter, or None unbound.

    Never resolve the executable itself: separate venvs can share base Python.
    External, missing, ambiguous, and symlinked venv directories are unsupported.
    """
    if _target is None:
        return None
    candidates = [_target / name for name in ('venv', '.venv')
                  if (_target / name).exists() or (_target / name).is_symlink()]
    if len(candidates) != 1:
        raise ValueError('target requires exactly one local virtual environment')
    venv = candidates[0]
    if venv.is_symlink() or not venv.is_dir():
        raise ValueError('target virtual environment must be a physical directory')
    config = venv / 'pyvenv.cfg'
    bindir = venv / ('Scripts' if os.name == 'nt' else 'bin')
    python = bindir / ('python.exe' if os.name == 'nt' else 'python')
    if (bindir.is_symlink() or not bindir.is_dir() or config.is_symlink()
            or not config.is_file() or not python.is_file()):
        raise ValueError('target virtual environment metadata or interpreter missing')
    if hasattr(os, 'getuid') and any(p.stat().st_uid != os.getuid() for p in (venv, bindir, config)):
        raise ValueError('target virtual environment is not owned by current user')
    return python


def validate_recovery_source():
    """Refuse recovery until the bound checkout is at its prepared commit."""
    if _identities is None:
        return
    target = _identities[1]
    commit = prepared_staged_commit()
    _physical_root(target)
    _verify_git(target, commit)
    package = target / 'clover_cli'
    if package.is_symlink() or not package.is_dir():
        raise ValueError('target recovery package is not a physical directory')
    for relative in ('clover_cli/__init__.py', 'clover_cli/update_restart_recovery.py'):
        path = target / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError('target recovery source is missing or symlinked')
        result = subprocess.run(
            ['git', '-C', str(target), 'ls-files', '--error-unmatch', '--', relative],
            env={'PATH': os.defpath, 'HOME': str(target / '.git'),
                 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                 'GIT_NO_REPLACE_OBJECTS': '1'},
            capture_output=True, check=False,
        )
        if result.returncode:
            raise ValueError('target recovery source is not tracked')


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
           'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_OPTIONAL_LOCKS': '0', 'LC_ALL': 'C'}
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


def staged_preparation_pending():
    """True while a bound staged invocation has not validated its fetched tip."""
    return _identities is not None and _prepared_commit is None


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
def bind_target(stage, target, expected_stage_commit, expected_target_commit, *, dependency_paths=()):
    """Bind one validated target for the duration of a controller invocation.

    Identities must come from a trusted caller, never from the checkout itself.
    Nested or concurrent bindings are rejected, even to the same target.
    """
    global _target, _identities, _prepared_commit, _trusted_dependencies
    if not _lock.acquire(blocking=False):
        raise RuntimeError('a migration target is already bound')
    try:
        stage = _physical_root(stage)
        target = _physical_root(target)
        if stage == target or stage in target.parents or target in stage.parents:
            raise ValueError('stage and target must be disjoint')
        _verify_git(stage, expected_stage_commit)
        _verify_git(target, expected_target_commit)
        trusted = []
        for raw in dependency_paths:
            path = Path(raw)
            if not path.is_absolute() or not path.is_dir() or path.resolve() != path:
                raise ValueError("trusted dependencies must be physical absolute directories")
            if any(path == root or root in path.parents or path in root.parents for root in (stage, target)):
                raise ValueError("trusted dependencies must be disjoint from source")
            stat = path.stat()
            trusted.append((path, (stat.st_dev, stat.st_ino)))
        _trusted_dependencies = tuple(trusted)
        _target = target
        _identities = (stage, target, expected_stage_commit, expected_target_commit)
        try:
            yield target
        finally:
            _target = None
            _identities = None
            _prepared_commit = None
            _trusted_dependencies = ()
    finally:
        _lock.release()
