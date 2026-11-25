"""``clover update`` cleanup must never touch another Clover home's dashboard."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clover_cli import dashboard_procs as dp
from clover_cli import main as m


@pytest.fixture
def two_homes(tmp_path, monkeypatch):
    mine = tmp_path / "mine"
    other = tmp_path / "other"
    mine.mkdir()
    other.mkdir()
    monkeypatch.setenv("CLOVER_HOME", str(mine))
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    return mine, other


def _show_run(env_home, main_pid=0):
    def fake_run(args, *a, **kw):
        args = list(args)
        if "list-unit-files" in args:
            return MagicMock(returncode=0, stdout="clover-dashboard.service enabled enabled\n", stderr="")
        if "show" in args:
            env = f"Environment=CLOVER_HOME={env_home}\n" if env_home else "Environment=\n"
            return MagicMock(returncode=0, stdout=env + f"MainPID={main_pid}\n", stderr="")
        if "is-active" in args:
            return MagicMock(returncode=0, stdout="active\n", stderr="")
        if "is-enabled" in args:
            return MagicMock(returncode=0, stdout="enabled\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return fake_run


def _restart_calls(run):
    return [c for c in run.call_args_list if "restart" in list(c.args[0])]


def test_unit_of_other_home_is_not_restarted(two_homes):
    _mine, other = two_homes
    with patch("subprocess.run", side_effect=_show_run(other)) as run:
        assert m._restart_managed_dashboard_service("x") is False
    assert _restart_calls(run) == []


def test_unit_of_this_home_is_restarted(two_homes):
    mine, _other = two_homes
    with patch("subprocess.run", side_effect=_show_run(mine)) as run:
        assert m._restart_managed_dashboard_service("x") is True
    assert len(_restart_calls(run)) == 1


def test_unit_with_unreadable_home_is_not_mine_for_non_default_home(two_homes):
    with patch("subprocess.run", side_effect=_show_run(None, 0)) as run:
        assert m._restart_managed_dashboard_service("x") is False
    assert _restart_calls(run) == []


def test_unit_with_unreadable_home_is_mine_for_default_home(tmp_path, monkeypatch):
    home = tmp_path / "h"
    (home / ".clover").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLOVER_HOME", str(home / ".clover"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    with patch("subprocess.run", side_effect=_show_run(None, 0)) as run:
        assert m._restart_managed_dashboard_service("x") is True
    assert len(_restart_calls(run)) == 1


def test_unit_home_read_from_main_pid_when_env_missing(two_homes):
    mine, other = two_homes
    with patch("subprocess.run", side_effect=_show_run(None, 4242)), \
         patch("clover_cli.dashboard_procs._clover_home_for_pid", return_value=str(other)):
        assert m._restart_managed_dashboard_service("x") is False


def test_find_stale_pids_drops_other_homes_and_unreadable(two_homes):
    mine, other = two_homes
    homes = {1: str(mine), 2: str(other), 3: None}
    procs = [(1, "clover serve"), (2, "clover serve"), (3, "clover serve")]
    with patch("clover_cli.main._scan_dashboard_processes", return_value=procs), \
         patch("clover_cli.dashboard_procs._clover_home_for_pid", side_effect=lambda p: homes[p]):
        assert m._find_stale_dashboard_pids() == [1]


def test_kill_only_signals_own_home(two_homes):
    mine, other = two_homes
    homes = {11: str(mine), 22: str(other)}
    with patch("clover_cli.main._restart_managed_dashboard_service", return_value=False), \
         patch("clover_cli.main._scan_dashboard_processes",
                      return_value=[(11, "clover serve"), (22, "clover serve")]), \
         patch("clover_cli.dashboard_procs._clover_home_for_pid", side_effect=lambda p: homes.get(p)), \
         patch("gateway.status._pid_exists", return_value=False), \
         patch("os.kill") as kill:
        dp._kill_stale_dashboard_processes()
    signalled = {c.args[0] for c in kill.call_args_list}
    assert 22 not in signalled


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process env")
def test_e2e_two_homes_cleanup_leaves_other_home_alive(tmp_path):
    """Real processes: cleaning home A must leave home B's process alive."""
    code = "import time; time.sleep(60)"
    # argv must look like a dashboard so the real scan matches it.
    def spawn(home):
        env = dict(os.environ, CLOVER_HOME=str(home))
        return subprocess.Popen(
            ["bash", "-c", "exec -a 'clover serve --port 0' python3 -c \"$0\"", code],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    pa, pb = spawn(a), spawn(b)
    try:
        time.sleep(0.8)
        env = dict(os.environ, CLOVER_HOME=str(a), HOME=str(tmp_path))
        out = subprocess.run(
            [sys.executable, "-c",
             "from clover_cli import main as m; print(m._find_stale_dashboard_pids())"],
            env=env, capture_output=True, text=True, timeout=60,
            cwd=str(Path(__file__).resolve().parents[2]),
        ).stdout
        assert str(pa.pid) in out
        assert str(pb.pid) not in out
        # Real cleanup of home A.
        subprocess.run(
            [sys.executable, "-c",
             "from clover_cli import dashboard_procs as d; d._kill_stale_dashboard_processes()"],
            env=dict(env), capture_output=True, text=True, timeout=60,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        pa.wait(timeout=10)
        assert pb.poll() is None, "home B's process was killed by home A cleanup"
    finally:
        for p in (pa, pb):
            if p.poll() is None:
                p.kill()
