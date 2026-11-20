"""Run the post-update stale dashboard/serve cleanup in a fresh process.

``clover update`` executes in the PRE-update interpreter, so any code it
imported earlier (``clover_cli.main`` and friends) is the OLD code. The
cleanup must use the newly installed matcher, so the updater launches this
module with the venv python as a child: ``python -m clover_cli.update_fresh_cleanup``.

The child prints the normal cleanup output, then one final line
``CLOVER_CLEANUP_RESULT {json}`` that the parent parses and strips.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

MARKER = "CLOVER_CLEANUP_RESULT "


def _summarize(result: dict) -> str:
    killed = len(result.get("killed") or [])
    failed = len(result.get("failed") or [])
    matched = len(result.get("matched") or [])
    if not matched:
        return "  ✓ Stale-copy check: no old dashboard/serve processes found."
    msg = f"  ✓ Stale-copy check: stopped {killed} old dashboard/serve process(es)"
    if failed:
        msg += f", {failed} could not be stopped"
    ssh = len(result.get("ssh_outdated") or [])
    if ssh:
        msg += f" (including {ssh} outdated desktop copy over SSH)"
    return msg + ". The desktop app starts a new backend on the new code by itself."


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="update_fresh_cleanup")
    p.add_argument("--restart-managed", action="store_true")
    p.add_argument("--already-restarted", default="")
    args = p.parse_args(argv)
    units = {u for u in args.already_restarted.split(",") if u} or None
    from clover_cli import main as _main

    result = _main._kill_stale_dashboard_processes(
        restart_managed=args.restart_managed, already_restarted_units=units
    )
    print(MARKER + json.dumps(result, default=str))
    return 0


def run_in_fresh_process(
    already_restarted_units: "set[str] | None" = None, timeout: float = 90.0
) -> "dict | None":
    """Run the cleanup in a child of the installed code. None on any failure."""
    cmd = [sys.executable, "-m", "clover_cli.update_fresh_cleanup", "--restart-managed"]
    if already_restarted_units:
        cmd += ["--already-restarted", ",".join(sorted(already_restarted_units))]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
            env=dict(os.environ),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    result = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith(MARKER):
            try:
                result = json.loads(line[len(MARKER):])
            except ValueError:
                result = None
        else:
            print(line)
    if proc.returncode != 0 or result is None:
        return None
    return result


if __name__ == "__main__":
    raise SystemExit(main())
