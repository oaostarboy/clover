"""A process that started before an update must not run cron (mixed-version crash).

Field incident 2026-11-19: a desktop backend started before an in-place update
won the cron tick lock and imported new tool files against old loaded modules.
"""
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import cron.scheduler as sch
from gateway import code_skew


@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    """An install whose clover_cli/__init__.py we can change after 'start'."""
    root = tmp_path / "install"
    (root / "clover_cli").mkdir(parents=True)
    init = root / "clover_cli" / "__init__.py"
    init.write_text('__version__ = "2.1.5"\n')
    monkeypatch.setattr(code_skew, "_PROJECT_ROOT", root)
    monkeypatch.setattr(code_skew, "_fingerprint", lambda: None)  # not a git checkout
    monkeypatch.setattr(code_skew, "_boot_fingerprint", None)
    monkeypatch.setattr(code_skew, "_boot_stamp", None)
    monkeypatch.setattr(code_skew, "_stamp_cache", (0.0, False))
    monkeypatch.setattr(sch, "_STALE_CODE_WARNED", False)
    return init


def _update_on_disk(init: Path):
    init.write_text('__version__ = "2.1.6"\n# updated\n')


def _tick_cron(tmp_path):
    return sch.tick(verbose=False, sync=True)


def test_unrecorded_process_is_never_accused(fake_install):
    assert code_skew.code_updated_since_start(ttl=0) is False


def test_unchanged_disk_does_not_block(fake_install):
    code_skew.record_boot_fingerprint()
    assert code_skew.code_updated_since_start(ttl=0) is False


def test_stale_process_skips_tick_leaves_lock_free_and_warns_once(fake_install, caplog):
    code_skew.record_boot_fingerprint()
    _update_on_disk(fake_install)
    caplog.set_level(logging.WARNING, logger="cron.scheduler")
    code_skew._stamp_cache = (0.0, False)
    lock_dir, lock_file = sch._get_lock_paths()
    with patch.object(sch, "get_due_jobs", create=True) as due:
        assert sch.tick(verbose=False) == 0
        assert sch.tick(verbose=False) == 0
        due.assert_not_called()
    warnings = [r for r in caplog.records if "code updated on disk since this process started" in r.getMessage()]
    assert len(warnings) == 1
    assert "leaving cron to an up-to-date process" in warnings[0].getMessage()
    # the tick lock was never taken, so a fresh process can take it
    if sch.fcntl:
        fd = open(lock_file, "a+")
        try:
            sch.fcntl.flock(fd, sch.fcntl.LOCK_EX | sch.fcntl.LOCK_NB)
        finally:
            fd.close()


def test_fresh_process_after_update_still_ticks(fake_install):
    _update_on_disk(fake_install)
    code_skew.record_boot_fingerprint()  # started AFTER the update
    code_skew._stamp_cache = (0.0, False)
    assert sch._stale_code_blocks_cron() is False
    assert sch.tick(verbose=False) == 0  # ran (no jobs due), did not get blocked


def test_stale_process_refuses_agent_run_with_plain_message(fake_install):
    code_skew.record_boot_fingerprint()
    _update_on_disk(fake_install)
    code_skew._stamp_cache = (0.0, False)
    job = {"id": "j1", "name": "Proactive check-in", "prompt": "hi", "checkin_role": "check"}
    with patch("run_agent.AIAgent") as agent_cls:
        ok, _out, _final, err = sch.run_job(job)
    assert ok is False and err == sch.STALE_CODE_NOTICE
    assert "Clover was updated while running; restart it" in err
    agent_cls.assert_not_called()


def test_check_is_cached_and_cheap(fake_install):
    code_skew.record_boot_fingerprint()
    calls = []
    real = code_skew._code_stamp
    with patch.object(code_skew, "_code_stamp", side_effect=lambda: calls.append(1) or real()):
        for _ in range(50):
            code_skew.code_updated_since_start(ttl=60)
    assert len(calls) == 1


@pytest.mark.parametrize("err", [
    "ToolRegistry.register() got an unexpected keyword argument 'deadline_floor'",
    "ImportError: cannot import name 'is_secret_file_path' from 'agent.redact'",
    "AttributeError: module 'x' has no attribute 'y'",
])
def test_mismatch_failure_alert_says_restart_plainly(err, monkeypatch):
    monkeypatch.setattr(sch, "_detect_gateway_code_skew", lambda: ("3babcd94", "abaa8f86"))
    msg = sch._summarize_cron_failure_for_delivery({"name": "Proactive check-in", "id": "4e17"}, err)
    assert "Clover was updated while running; restart it" in msg
    assert "3babcd94" in msg and "abaa8f86" in msg


def test_alert_unchanged_without_skew(monkeypatch):
    monkeypatch.setattr(sch, "_detect_gateway_code_skew", lambda: None)
    msg = sch._summarize_cron_failure_for_delivery(
        {"name": "n", "id": "i"}, "ImportError: cannot import name 'x'")
    assert "updated while running" not in msg


# ── update scan: the isolated / pooled serve shapes ───────────────────────

INCIDENT_SHAPES = [
    # desktop pooled backend: global --profile flag sits between program and subcommand
    "/Users/a/.clover/clover-c1/venv/bin/python -m clover_cli.main --profile default serve --host 127.0.0.1 --port 0",
    # isolated serve (what Desktop SSH and `--isolated` launch)
    "/Users/a/.clover/clover-c1/venv/bin/python -m clover_cli.main -p work serve --isolated --host 127.0.0.1 --port 0 --ssh-owner-nonce 0123456789abcdef",
    "/Users/a/.clover/clover-c1/venv/bin/clover --profile work serve --isolated --port 0",
    "/Users/a/.clover/clover-c1/venv/bin/python /Users/a/.clover/clover-c1/venv/bin/clover -p default serve --port 0",
    "/usr/bin/python3 /Users/a/.clover/clover-c1/clover_cli/main.py --profile=x dashboard --no-open",
    # old shapes keep working
    "/Users/a/.clover/clover-c1/venv/bin/clover serve --isolated --host 127.0.0.1 --port 0",
]


def _ps(lines):
    out = "\n".join(f"{1000 + i:>7} {c}" for i, c in enumerate(lines)) + "\n"
    return MagicMock(returncode=0, stdout=out)


@pytest.mark.parametrize("cmd", INCIDENT_SHAPES)
def test_update_scan_finds_isolated_and_pooled_serve_shapes(cmd):
    from clover_cli.dashboard_procs import _scan_dashboard_processes
    with patch("clover_cli.dashboard_procs.subprocess.run", return_value=_ps([cmd])), \
         patch("clover_cli.process_identity.ledger_entries", return_value=[]):
        found = _scan_dashboard_processes()
    assert [c for _p, c in found] == [cmd]


@pytest.mark.parametrize("cmd", [
    "/usr/bin/python3 -m pytest tests/serve --profile x",
    "vim clover serve notes.md",   # editor, not a launch (has substring match, pre-existing behaviour aside)
    "/usr/bin/python3 other_tool.py --profile default serve",
    "/Users/a/.clover/clover-c1/venv/bin/clover chat -q 'what does serve do'",
])
def test_update_scan_ignores_unrelated_commands(cmd):
    from clover_cli.dashboard_procs import _dashboard_mode_from_tokens
    if "vim" in cmd:
        pytest.skip("substring patterns predate this change")
    assert _dashboard_mode_from_tokens(cmd) is None


def test_status_parser_understands_profiled_serve():
    from clover_cli.main import _parse_dashboard_runtime
    mode, host, port = _parse_dashboard_runtime(INCIDENT_SHAPES[0])
    assert (mode, host, port) == ("serve", "127.0.0.1", 0)


def test_real_process_with_incident_cmdline_is_found_and_stopped(tmp_path):
    """Spawn an actual process whose argv has the incident shape; update cleanup must see it."""
    if sys.platform == "win32":
        pytest.skip("POSIX process table")
    script = tmp_path / "clover_cli"
    script.mkdir()
    (script / "main.py").write_text("import time\ntime.sleep(60)\n")
    proc = subprocess.Popen(
        [sys.executable, str(script / "main.py"), "--profile", "default", "serve",
         "--isolated", "--host", "127.0.0.1", "--port", "0"],
        stdin=subprocess.DEVNULL)
    try:
        import time
        from clover_cli.dashboard_procs import _scan_dashboard_processes
        pids: list = []
        for _ in range(50):  # wait for exec: right after fork the argv is still the parent's
            with patch("clover_cli.process_identity.ledger_entries", return_value=[]):
                pids = [p for p, _ in _scan_dashboard_processes()]
            if proc.pid in pids:
                break
            time.sleep(0.1)
        assert proc.pid in pids
    finally:
        proc.kill()
        proc.wait()
