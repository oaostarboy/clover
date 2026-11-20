"""Stale-copy cleanup must run with the NEWLY installed code (fresh child)."""
import os
import subprocess
import sys
import time

import pytest

from clover_cli import update_cmd, update_fresh_cleanup

pytestmark = pytest.mark.real_fresh_cleanup

ORACLE_ARGV = [
    "clover", "serve", "--isolated", "--host", "127.0.0.1", "--port", "0",
    "--ssh-session-token-file", "/tmp/x-token", "--ssh-owner-nonce",
    "0123456789abcdef",
]


@pytest.fixture
def dummy(tmp_path):
    """A harmless sleeper whose argv looks like the desktop's serve backend."""
    script = tmp_path / "clover"
    script.write_text("import time\ntime.sleep(600)\n")
    proc = subprocess.Popen(
        [sys.executable, str(script)] + ORACLE_ARGV[1:],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
    )
    time.sleep(0.3)
    yield proc
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def _alive(p):
    return p.poll() is None


def test_child_finds_and_stops_dummy_even_if_old_matcher_misses(
    dummy, monkeypatch, capsys, tmp_path
):
    # Old in-memory scan in THIS process misses everything.
    from clover_cli import main as m
    monkeypatch.setattr(m, "_scan_dashboard_processes", lambda **k: [])
    monkeypatch.setattr(m, "_find_stale_dashboard_pids", lambda **k: [])
    monkeypatch.setattr(update_cmd, "_reload_process_scan_modules", lambda: None)
    # SAFETY: the child scans the REAL process table. A sitecustomize in the
    # child narrows the scan result to our dummy PID so nothing else is touched.
    sc = tmp_path / "sc"
    sc.mkdir()
    (sc / "sitecustomize.py").write_text(
        "import os\n"
        "from clover_cli import dashboard_procs as d\n"
        "_orig = d._scan_dashboard_processes\n"
        "def _only(**k):\n"
        "    ok = int(os.environ['ONLY_PID'])\n"
        "    return [(p, c) for p, c in _orig(**k) if p == ok]\n"
        "d._scan_dashboard_processes = _only\n"
        "from clover_cli import main as m\n"
        # the dummy lives in the test runner's cgroup (possibly a REAL unit):
        # never let the cleanup restart that unit.
        "m._get_systemd_service_for_pid = lambda pid: None\n"
    )
    monkeypatch.setenv("ONLY_PID", str(dummy.pid))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(sc)] + sys.path))
    update_cmd._finish_dashboard_update_cleanup([])
    out = capsys.readouterr().out
    assert dummy.wait(timeout=10) is not None
    assert not _alive(dummy)
    assert "Stale-copy check: stopped 1" in out
    log = os.path.join(os.path.dirname(os.environ["CLOVER_HOME"]), "stub.log")
    if os.path.exists(log):  # only under safe-run.sh
        calls = open(log).read().splitlines()
        assert all("list-unit-files" in c for c in calls), calls


def test_outcome_line_when_nothing_found(monkeypatch, capsys):
    monkeypatch.setattr(
        update_fresh_cleanup, "run_in_fresh_process",
        lambda *a, **k: {"matched": [], "killed": [], "failed": []},
    )
    update_cmd._finish_dashboard_update_cleanup([])
    assert "no old dashboard/serve processes found" in capsys.readouterr().out


def test_fallback_when_child_fails(monkeypatch, capsys):
    from clover_cli import main as m
    monkeypatch.setattr(update_fresh_cleanup, "run_in_fresh_process", lambda *a, **k: None)
    monkeypatch.setattr(update_cmd, "_reload_process_scan_modules", lambda: None)
    calls = []
    monkeypatch.setattr(
        m, "_kill_stale_dashboard_processes",
        lambda **k: calls.append(k) or {"matched": [], "killed": [], "failed": [], "unrecovered": []},
    )
    update_cmd._finish_dashboard_update_cleanup([], {"clover-gateway"})
    assert calls == [{"restart_managed": True, "already_restarted_units": {"clover-gateway"}}]
    assert "fallback" in capsys.readouterr().out


def test_child_crash_returns_none(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/nonexistent/python")
    assert update_fresh_cleanup.run_in_fresh_process() is None


def test_node_failure_skips_everything(monkeypatch, capsys):
    monkeypatch.setattr(
        update_fresh_cleanup, "run_in_fresh_process",
        lambda *a, **k: pytest.fail("must not run"),
    )
    update_cmd._finish_dashboard_update_cleanup(["node"])
