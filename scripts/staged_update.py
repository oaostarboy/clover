#!/usr/bin/env python3
"""Explicit first-upgrade entry, run from an operator-trusted staged checkout.

The caller must obtain this entry and its expected commit/tree from a trusted
source. These checks bind that trust; this script cannot authenticate itself.
It deliberately does not retrofit an already-loaded old /update handler.
"""
import sys


def main(argv=None):
    if not sys.flags.isolated or not sys.flags.no_site:
        print('staged update requires -I -S (isolated startup without site hooks)', file=sys.stderr)
        return 2
    # No product imports, site.addsitedir, target code, or target cwd lookup.
    import argparse
    import os
    from pathlib import Path
    import re
    import subprocess
    from types import SimpleNamespace

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, type=Path)
    parser.add_argument('--old-commit', required=True)
    parser.add_argument('--stage-commit', required=True)
    parser.add_argument('--stage-tree', required=True)
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--dependency-path', action='append', default=[], type=Path,
                        help='explicit trusted runtime directory; .pth files are NOT processed')
    options = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    try:
        if sys.platform not in ('linux', 'darwin'):
            raise ValueError('staged migration currently supports POSIX Git installations only')
        for value in (options.old_commit, options.stage_commit, options.stage_tree):
            if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value):
                raise ValueError('identities must be full lowercase Git object IDs')
        stage = Path(__file__).resolve().parent.parent
        target = options.target
        home = options.home
        for path in (target, home):
            if not path.is_absolute() or not path.is_dir() or path != path.resolve():
                raise ValueError('target and home must be existing absolute physical directories')
        if stage == target or stage in target.parents or target in stage.parents:
            raise ValueError('stage and target must be disjoint')
        for path in (stage, target):
            if home == path or path in home.parents or home in path.parents:
                raise ValueError('profile home must be separate from source checkouts')
        if 'clover_cli.main' in sys.modules:
            raise ValueError('staged entry requires a fresh interpreter')

        # Eliminate inherited Git/Python startup controls. Every Git subprocess,
        # including ordinary updater calls, inherits these command-scope settings.
        for key in list(os.environ):
            if key.startswith(('GIT_', 'PYTHON', 'LD_', 'DYLD_')):
                os.environ.pop(key, None)
        os.environ.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                           'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_TERMINAL_PROMPT': '0',
                           'CLOVER_HOME': str(home), 'PYTHONDONTWRITEBYTECODE': '1'})
        overrides = [('core.hooksPath', os.devnull), ('core.fsmonitor', 'false'),
                     ('credential.helper', ''), ('protocol.ext.allow', 'never'),
                     ('submodule.recurse', 'false')]
        os.environ['GIT_CONFIG_COUNT'] = str(len(overrides))
        for index, (key, value) in enumerate(overrides):
            os.environ[f'GIT_CONFIG_KEY_{index}'] = key
            os.environ[f'GIT_CONFIG_VALUE_{index}'] = value

        def git(root, *args):
            result = subprocess.run(['git', '-C', str(root), *args],
                                    text=True, capture_output=True, timeout=30)
            if result.returncode:
                raise ValueError('cannot verify staged Git source')
            return result.stdout.strip()

        # A clean status can run a configured filter. Reject that configuration
        # before checking source bytes. No target modules have been imported.
        for root in (stage, target):
            cfg = git(root, 'config', '--local', '--includes', '--list')
            if any(line.lower().startswith(('filter.', 'core.sshcommand', 'url.'))
                   for line in cfg.splitlines()):
                raise ValueError('executable Git configuration is unsupported for migration')
        if git(stage, 'rev-parse', 'HEAD') != options.stage_commit:
            raise ValueError('stage commit mismatch')
        if git(stage, 'rev-parse', 'HEAD^{tree}') != options.stage_tree:
            raise ValueError('stage tree mismatch')
        # Ignored shadow modules count too; stage has no venv or generated files.
        if git(stage, 'status', '--porcelain=v1', '--untracked-files=all', '--ignored'):
            raise ValueError('stage must contain only its clean pinned source')
        if git(stage, 'ls-files', '--stage').find('120000 ') >= 0:
            raise ValueError('stage symlinks are not supported')

        dependencies = []
        for path in options.dependency_path:
            if not path.is_absolute() or not path.is_dir() or path != path.resolve():
                raise ValueError('dependency path must be an explicit trusted physical directory')
            if any(path == root or root in path.parents or path in root.parents
                   for root in (target, stage, home)):
                raise ValueError('dependency path must be separate from source and profile')
            dependencies.append(str(path))
        # -I -S leaves stdlib paths only. Add staged code and explicitly selected
        # trusted package dirs directly, never evaluating .pth/sitecustomize.
        sys.path[:0] = [str(stage), *dependencies]
        os.chdir(stage)
        sys.argv = ['clover', 'update', '--yes', '--branch', 'main']
        from clover_cli._migration_target import bind_target
        with bind_target(stage, target, options.stage_commit, options.old_commit):
            from clover_cli import main as cli
            if not Path(cli.__file__).resolve().is_relative_to(stage):
                raise ValueError('updater code did not load from pinned stage')
            # Maintenance argv suppresses external secret command loading in
            # the normal CLI. The usual lock, preflight, lifecycle, writer and
            # receipt boundaries remain owned by cmd_update.
            cli.cmd_update(SimpleNamespace(yes=True, branch='main', gateway=False,
                                           force=False, force_venv=False,
                                           switch_branch=False, keep_stash=False,
                                           plan=False, check=False))
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'staged update refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
