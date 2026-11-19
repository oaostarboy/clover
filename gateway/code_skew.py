"""Detect when the gateway is running stale code after a hot ``git pull``.

The gateway is a single long-lived process; its ``sys.modules`` is frozen at
boot. If the checkout is updated underneath it (a manual ``git pull``, or the
window before ``clover update``'s graceful restart fires), a first-time lazy
import on a new code path can resolve a freshly-pulled consumer module against a
stale cached dependency -> ImportError (see
``tests/test_stale_utils_module_import.py`` for the exact failure).

We snapshot the checkout revision at gateway startup and compare on demand, so
risky callers (e.g. ``/model`` switching) can refuse with a clear "restart the
gateway" message instead of crashing on a cryptic import error.

If the revision can't be read (non-git install, IO error), the boot snapshot
stays ``None`` and skew detection no-ops — it never produces a false positive.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_boot_fingerprint: str | None = None
_boot_stamp: str | None = None
_stamp_cache: tuple[float, bool] = (0.0, False)
_STAMP_TTL_SECONDS = 5.0
_VERSION_RE = re.compile(r"""__version__\s*=\s*['"]([^'"]+)['"]""")


def _version_stamp() -> str | None:
    """``ver:<__version__>:<size>:<mtime>`` of clover_cli/__init__.py, read from disk.

    One small file read plus a stat (no git, no import), so it also works for
    installs that are not git checkouts (a copied runtime, a pip install).
    """
    path = _PROJECT_ROOT / "clover_cli" / "__init__.py"
    try:
        st = path.stat()
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return None
    m = _VERSION_RE.search(head)
    return f"ver:{m.group(1) if m else '?'}:{st.st_size}:{st.st_mtime_ns}"


def _code_stamp() -> str | None:
    parts = [p for p in (_fingerprint(), _version_stamp()) if p]
    return "|".join(parts) or None


def _fingerprint() -> str | None:
    """Current checkout fingerprint, reusing the CLI's git-rev reader.

    ``clover_cli.main`` is always already imported in a gateway process (it's
    the entry point), so this import is free and avoids duplicating the
    worktree-aware ref resolution.
    """
    try:
        from clover_cli.main import _read_git_revision_fingerprint

        return _read_git_revision_fingerprint(_PROJECT_ROOT)
    except Exception:
        return None


def record_boot_fingerprint() -> None:
    """Snapshot the checkout revision at gateway startup (idempotent)."""
    global _boot_fingerprint, _boot_stamp
    if _boot_fingerprint is None:
        _boot_fingerprint = _fingerprint()
    if _boot_stamp is None:
        _boot_stamp = _code_stamp()


def code_updated_since_start(*, ttl: float = _STAMP_TTL_SECONDS) -> bool:
    """True when the code on disk differs from what this process started with.

    Used by the cron ticker: a process that started before ``clover update``
    holds old modules in memory and would load new files against them, so it
    must not run scheduled jobs. Cheap (cached for ``ttl`` seconds, file reads
    only). ``False`` when no boot stamp was recorded, so it never accuses a
    process it knows nothing about.
    """
    global _stamp_cache
    if _boot_stamp is None:
        return False
    now = time.monotonic()
    when, value = _stamp_cache
    if when and now - when < ttl:
        return value
    value = _code_stamp() != _boot_stamp
    _stamp_cache = (now, value)
    return value


def _short(fingerprint: str) -> str:
    """Render a ``git:<ref>:<sha>`` fingerprint as a compact label."""
    sha = fingerprint.rsplit(":", 1)[-1]
    if sha and sha != "unresolved" and len(sha) > 10:
        return sha[:10]
    return sha or fingerprint


def detect_code_skew() -> tuple[str, str] | None:
    """Return ``(boot_rev, disk_rev)`` short labels if the checkout drifted
    since boot, else ``None``."""
    if _boot_fingerprint is not None:
        current = _fingerprint()
        if current is not None and current != _boot_fingerprint:
            return _short(_boot_fingerprint), _short(current)
        return None
    if _boot_stamp is not None:
        # Not a git checkout: fall back to the version file stamp.
        current = _code_stamp()
        if current is not None and current != _boot_stamp:
            return _short_version(_boot_stamp), _short_version(current)
    return None


def _short_version(stamp: str) -> str:
    m = re.search(r"ver:([^:|]+)", stamp)
    return f"v{m.group(1)}" if m else "unknown"
