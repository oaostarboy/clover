"""Discover the files outside ``apps/desktop`` that the desktop build consumes.

The desktop bundle is not built from ``apps/desktop`` alone: ``electron/main.ts``
imports ``services/managed-relay/src/*.mjs`` and the renderer aliases
``@clover/shared`` to ``apps/shared/src``. A content hash that only walks
``apps/desktop`` therefore says "up to date" after a release that changed only
those vendored inputs, and the installed app keeps the old code.

Instead of keeping a hand list that drifts, derive the extra inputs from the
build's own references:

* relative import / export / require / dynamic-import specifiers in the desktop
  sources that resolve outside ``apps/desktop`` (followed transitively), and
* relative path literals in the build configs (``vite.config``, ``tsconfig*``,
  the bundler script), which name alias targets and ``include`` roots.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Iterable, Optional

_SOURCE_SUFFIXES = {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}
_RESOLVE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".json")
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", "release", "coverage", "__pycache__"}

# `from '../x'`, `import '../x'`, `import('../x')`, `require('../x')`
_SPECIFIER = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*)["'](\.{1,2}/[^"'\n]+)["']"""
)
# Any quoted relative path in a build config (alias targets, include roots).
_CONFIG_PATH = re.compile(r"""["'](\.{1,2}/[^"'\n*]+)["']""")


def _resolve_file(base: Path) -> Optional[Path]:
    candidates = [base]
    # `./x.js` in TS sources frequently means `./x.ts`.
    if base.suffix in (".js", ".mjs", ".cjs", ".jsx"):
        stem = base.with_suffix("")
        candidates += [stem.with_suffix(s) for s in (".ts", ".tsx", ".mts", ".cts")]
    candidates += [Path(str(base) + s) for s in _RESOLVE_SUFFIXES]
    candidates += [base / f"index{s}" for s in _RESOLVE_SUFFIXES]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _walk_dir(root: Path, is_ignored: Callable[[Path], bool]) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _SKIP_DIRS and not is_ignored(Path(dirpath) / d)
        )
        for fn in sorted(filenames):
            fp = Path(dirpath) / fn
            if not is_ignored(fp):
                yield fp


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def external_desktop_build_inputs(
    project_root: Path,
    desktop_dir: Path,
    is_ignored: Callable[[Path], bool],
) -> list[Path]:
    """Return sorted files outside *desktop_dir* (and inside *project_root*) the build reads."""
    project_root = project_root.resolve()
    desktop_dir = desktop_dir.resolve()
    found: set[Path] = set()
    queue: list[Path] = []

    def consider(path: Path) -> None:
        try:
            path = path.resolve()
        except OSError:
            return
        if not _inside(path, project_root) or _inside(path, desktop_dir):
            return
        if any(part in _SKIP_DIRS for part in path.relative_to(project_root).parts):
            return
        if is_ignored(path):
            return
        if path.is_dir():
            # A directory that contains the desktop app (e.g. the repo root
            # used for fs.allow) is a sandbox boundary, not a build input.
            if _inside(desktop_dir, path):
                return
            for fp in _walk_dir(path, is_ignored):
                consider(fp)
            return
        if path in found:
            return
        found.add(path)
        if path.suffix in _SOURCE_SUFFIXES:
            queue.append(path)

    def scan(path: Path, pattern: re.Pattern[str]) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for match in pattern.finditer(text):
            target = (path.parent / match.group(1)).resolve()
            hit = _resolve_file(target)
            if hit is not None:
                consider(hit)
            elif target.is_dir():
                consider(target)

    # Desktop sources and configs reference the outside world.
    for fp in _walk_dir(desktop_dir, is_ignored):
        name = fp.name
        if (
            fp.suffix in _SOURCE_SUFFIXES
            and not name.endswith(".d.ts")
            and ".test." not in name
            and ".spec." not in name
        ):
            scan(fp, _SPECIFIER)
        if name.startswith(("vite.config", "vitest.config", "tsconfig")):
            scan(fp, _CONFIG_PATH)

    # Follow the vendored files' own relative imports.
    while queue:
        scan(queue.pop(), _SPECIFIER)

    return sorted(found)
