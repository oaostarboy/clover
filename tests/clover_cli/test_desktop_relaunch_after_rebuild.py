"""The updater must not leave a user without their desktop app.

Windows closes the running app before the pack (exe lock), so after a good
rebuild it must start ``clover desktop`` again. Nothing else changes: POSIX
never stops the app (it restarts itself when idle), and a failed build must not
relaunch anything.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from clover_cli import main as cli_main


def _ns():
    return argparse.Namespace(
        skip_build=False, build_only=True, force_build=False, source=False,
        fake_boot=False, ignore_existing=False, clover_root=None, cwd=None,
    )


def _run_build(tmp_path, monkeypatch, *, stopped, pack_rc=0):
    root = tmp_path / "clover-c1"
    desktop_dir = root / "apps" / "desktop"
    desktop_dir.mkdir(parents=True)
    (desktop_dir / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli_main, "PROJECT_ROOT", root)
    exe = desktop_dir / "release" / "app" / "Clover"
    exe.parent.mkdir(parents=True)
    exe.write_text("x")
    ok = subprocess.CompletedProcess(["npm"], 0)
    pack = subprocess.CompletedProcess(["npm"], pack_rc)
    with patch("clover_cli.main.shutil.which", return_value="/usr/bin/npm"), \
         patch("clover_cli.main._resolve_node_runtime_npm", return_value="npm"), \
         patch("clover_cli.main._run_npm_install_deterministic", return_value=ok), \
         patch("clover_cli.main._desktop_build_needed", return_value=True), \
         patch("clover_cli.main._stop_desktop_processes_locking_build", return_value=stopped), \
         patch("clover_cli.main._desktop_packaged_executable", return_value=exe), \
         patch("clover_cli.main._desktop_macos_relaunchable_fixup"), \
         patch("clover_cli.main._register_desktop_launchers"), \
         patch("clover_cli.main._write_desktop_build_stamp"), \
         patch("clover_cli.main._relaunch_desktop_after_rebuild") as relaunch, \
         patch("clover_cli.main.subprocess.run", return_value=pack):
        try:
            cli_main.cmd_gui(_ns())
        except SystemExit:
            pass
    return relaunch


def test_relaunches_after_rebuild_that_closed_the_app(tmp_path, monkeypatch):
    assert _run_build(tmp_path, monkeypatch, stopped=[123]).call_count == 1


def test_does_not_relaunch_when_app_was_left_running(tmp_path, monkeypatch):
    assert _run_build(tmp_path, monkeypatch, stopped=[]).call_count == 0


def test_does_not_relaunch_after_failed_build(tmp_path, monkeypatch):
    assert _run_build(tmp_path, monkeypatch, stopped=[123], pack_rc=1).call_count == 0


def test_relaunch_uses_the_canonical_clover_desktop_command():
    with patch("clover_cli.main.subprocess.Popen") as popen:
        assert cli_main._relaunch_desktop_after_rebuild() is True
    argv = popen.call_args.args[0]
    assert argv[-2:] == ["clover_cli.main", "desktop"] or argv[-1] == "desktop"
    assert "-m" in argv


def test_relaunch_failure_never_fails_the_update():
    with patch("clover_cli.main.subprocess.Popen", side_effect=OSError("boom")):
        assert cli_main._relaunch_desktop_after_rebuild() is False
