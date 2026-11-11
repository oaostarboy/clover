#!/usr/bin/env python3
"""Prepare a distinct update target in a disposable install-E2E checkout.

If a release tag already equals HEAD, retain that tag as the installed base and
create a child changing only a test marker. No production file or release ref is
changed. Call only in a disposable CI checkout, before the sandbox snapshots it.
"""
import argparse
from pathlib import Path
import subprocess


def prepare_target(repo: Path, install_ref: str) -> str:
    def git(*args: str) -> str:
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()

    if git('status', '--porcelain'):
        raise RuntimeError('Update-target preparation requires a clean checkout')
    candidate = git('rev-parse', 'HEAD^{commit}')
    base = git('rev-parse', '--verify', f'{install_ref}^{{commit}}')
    if base != candidate:
        return candidate
    marker = '.github/install-e2e/NEXT_MARKER'
    target_file = repo / marker
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(f'Install E2E successor of {candidate}\n', encoding='utf-8')
    git('add', '--', marker)
    tree = git('write-tree')
    target = git('-c', 'user.name=Clover install E2E',
                 '-c', 'user.email=install-e2e@example.invalid',
                 'commit-tree', tree, '-p', candidate,
                 '-m', 'test: marker-only next install target')
    changed = git('diff', '--name-only', candidate, target)
    if changed != marker:
        raise RuntimeError('Update fixture changed more than its marker')
    # Detach so a local main ref used as the install base remains untouched.
    git('checkout', '--detach', target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-ref', required=True)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(prepare_target(args.repo, args.install_ref))


if __name__ == '__main__':
    main()
