"""Shutdown cleanup must spare apps the agent deliberately detached.

Ownership rule (tools/process_registry.py ``_is_owned``): a process is still
ours iff it stayed in the worker's cgroup/scope and session. Normal jobs are
still killed; ``systemd-run --scope`` / ``setsid`` launches survive.
"""

import os
import shutil
import subprocess
import sys
import time

import pytest

from tools.process_registry import ProcessRegistry, ProcessSession

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process semantics")


def _alive(pid: int) -> bool:
    try:
        import psutil
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


def _wait(pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _user_scope_usable() -> bool:
    if sys.platform != "linux" or not shutil.which("systemd-run"):
        return False
    try:
        r = subprocess.run(
            ["systemd-run", "--user", "--scope", "--quiet", "true"],
            capture_output=True, timeout=10, stdin=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except Exception:
        return False


# --------------------------------------------------------------- unit: rule

class TestOwnershipRule:
    def test_same_cgroup_and_session_is_owned(self):
        assert ProcessRegistry._is_owned(
            "/user.slice/gw.service", 100,
            reference_cgroup="/user.slice/gw.service", unit="", session_sid=100,
        )

    def test_other_cgroup_without_unit_is_detached(self):
        assert not ProcessRegistry._is_owned(
            "/user.slice/app-x.scope", 100,
            reference_cgroup="/user.slice/gw.service", unit="", session_sid=100,
        )

    def test_worker_scope_membership(self):
        kw: dict = dict(reference_cgroup=None, session_sid=100)
        assert ProcessRegistry._is_owned(
            "/user.slice/clover-worker-p1.scope", 100, unit="clover-worker-p1.scope", **kw)
        assert not ProcessRegistry._is_owned(
            "/user.slice/app-electron.scope", 100, unit="clover-worker-p1.scope", **kw)

    def test_new_session_is_detached_but_root_exempt(self):
        kw: dict = dict(reference_cgroup=None, unit="", session_sid=100)
        assert not ProcessRegistry._is_owned(None, 555, **kw)
        assert ProcessRegistry._is_owned(None, 555, check_sid=False, **kw)

    def test_macos_semantics_no_cgroup_uses_session(self):
        # macOS: no /proc cgroup (None), only the session/pgid differs.
        kw: dict = dict(reference_cgroup=None, unit="", session_sid=100)
        assert ProcessRegistry._is_owned(None, 100, **kw)
        assert not ProcessRegistry._is_owned(None, 200, **kw)  # `open -a` / setsid

    def test_windows_semantics_unknown_never_disqualifies(self):
        # Windows: neither cgroup nor session exists -> historical tree kill.
        assert ProcessRegistry._is_owned(
            None, None, reference_cgroup=None, unit="", session_sid=None)

    def test_windows_classify_is_noop(self, monkeypatch):
        import tools.process_registry as pr
        monkeypatch.setattr(pr, "_IS_WINDOWS", True)
        s = ProcessSession(id="p", command="x", pid=123)
        assert ProcessRegistry()._classify_session_pids(s) == ([], [])


# ------------------------------------------------------------------- e2e

def _register(reg: ProcessRegistry, script: str, sid: str) -> ProcessSession:
    proc = subprocess.Popen(
        ["/bin/sh", "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    s = ProcessSession(id=sid, command=script, task_id="t", pid=proc.pid)
    s.process = proc
    s.host_start_time = reg._safe_host_start_time(proc.pid)
    reg._running[sid] = s
    return s


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path))
    monkeypatch.setattr(ProcessRegistry, "_daemon_term_grace_seconds",
                        staticmethod(lambda: 1.0))
    r = ProcessRegistry()
    yield r
    import psutil
    for p in psutil.process_iter(["cmdline"]):
        if (p.info["cmdline"] or [""])[0] == "clover-spare-test-daemon":
            try:
                p.kill()
            except Exception:
                pass


@posix_only
@pytest.mark.live_system_guard_bypass
def test_shutdown_kills_normal_job_but_spares_setsid_double_fork(reg, tmp_path):
    pidfile = tmp_path / "daemon.pid"
    normal = tmp_path / "normal.pid"
    # Detached app: double-fork + setsid, as nohup/setsid launchers do.
    s1 = _register(
        reg,
        f"(setsid sh -c 'exec -a clover-spare-test-daemon sleep 300 & echo $! > {pidfile}' &) ; sleep 300",
        "proc_detach",
    )
    # Ordinary background job with a child it still owns.
    s2 = _register(reg, f"sleep 301 & echo $! > {normal}; wait", "proc_normal")
    assert _wait(lambda: pidfile.exists() and pidfile.read_text().strip() and normal.exists()
                 and normal.read_text().strip())
    daemon = int(pidfile.read_text())
    child = int(normal.read_text())
    assert _alive(daemon) and _alive(child)

    killed = reg.kill_all(spare_detached=True)

    assert killed == 2
    assert _wait(lambda: not _alive(s1.pid)) and _wait(lambda: not _alive(s2.pid))
    assert _wait(lambda: not _alive(child)), "owned child of a normal job must die"
    assert _alive(daemon), "setsid-detached app must survive shutdown"
    os.kill(daemon, 9)


@posix_only
@pytest.mark.live_system_guard_bypass
def test_default_kill_all_still_kills_everything(reg, tmp_path):
    """Backward compat: without spare_detached nothing changes."""
    normal = tmp_path / "n.pid"
    s = _register(reg, f"sleep 302 & echo $! > {normal}; wait", "proc_n")
    assert _wait(lambda: normal.exists() and normal.read_text().strip())
    child = int(normal.read_text())
    reg.kill_all()
    assert _wait(lambda: not _alive(s.pid)) and _wait(lambda: not _alive(child))


@pytest.mark.live_system_guard_bypass
@pytest.mark.skipif(not _user_scope_usable(), reason="no usable systemd --user scopes")
def test_shutdown_spares_systemd_run_scope_app(reg, tmp_path):
    pidfile = tmp_path / "scoped.pid"
    unit = f"clover-spare-test-{os.getpid()}"
    s = _register(
        reg,
        f"systemd-run --user --scope --quiet --unit={unit} sh -c "
        f"'read p _ < /proc/self/stat; echo $p > {pidfile}.tmp; mv {pidfile}.tmp {pidfile}; exec sleep 303' & wait",
        "proc_scope",
    )
    assert _wait(lambda: pidfile.exists() and pidfile.read_text().strip())
    app = int(pidfile.read_text())
    assert _alive(app)
    try:
        owned, detached = reg._classify_session_pids(s)
        assert app in detached and s.pid in owned

        reg.kill_all(spare_detached=True)
        assert _wait(lambda: not _alive(s.pid))
        time.sleep(1.5)
        assert _alive(app), "scope-detached app must survive shutdown"
    finally:
        subprocess.run(["systemctl", "--user", "stop", f"{unit}.scope"],
                       capture_output=True, timeout=15)
        subprocess.run(["kill", "-9", str(app)], capture_output=True)
