"""Updater precheck must serialize, but must not hold a lock over its child."""
from pathlib import Path
import subprocess
import sys
import time

import pytest

from clover_cli import node_workspace as nw, update_cmd


def test_updater_precheck_fails_closed_when_workspace_busy(tmp_path, monkeypatch):
    desktop = tmp_path / 'apps' / 'desktop'
    desktop.mkdir(parents=True)
    (desktop / 'package.json').write_text('{}')
    (tmp_path / '.git').mkdir()
    (tmp_path / 'package.json').write_text('{}')
    ready = tmp_path / 'ready'
    script = '''from clover_cli.node_workspace import npm_workspace_lock
from pathlib import Path
import sys,time
with npm_workspace_lock(Path(sys.argv[1])):
    Path(sys.argv[2]).touch()
    time.sleep(60)
'''
    proc = subprocess.Popen([sys.executable, '-c', script, str(tmp_path), str(ready)])
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            assert proc.poll() is None
            time.sleep(.02)
        assert ready.exists()
        class Main:
            PROJECT_ROOT = tmp_path
            _resolve_node_runtime_npm = staticmethod(lambda: '/fake/npm')
            _desktop_build_needed = staticmethod(lambda *a, **k: False)
            _register_desktop_launchers = staticmethod(lambda *a, **k: None)
        monkeypatch.setattr(update_cmd, '_m', lambda: Main)
        real_lock = nw.npm_workspace_lock
        monkeypatch.setattr(nw, 'npm_workspace_lock', lambda root: real_lock(root, timeout=.2))
        with pytest.raises(TimeoutError, match='npm workspace lock'):
            update_cmd._rebuild_desktop_after_update(desktop, had_desktop_app_before_update=True)
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_updater_releases_precheck_lock_before_child(tmp_path, monkeypatch):
    desktop = tmp_path / 'apps' / 'desktop'
    desktop.mkdir(parents=True)
    (desktop / 'package.json').write_text('{}')
    (tmp_path / 'package.json').write_text('{}')
    marker = tmp_path / 'child-acquired'
    code = "from clover_cli.node_workspace import npm_workspace_lock; from pathlib import Path; import sys; exec('with npm_workspace_lock(Path(sys.argv[1]), timeout=2):\\n Path(sys.argv[2]).touch()')"
    class Main:
        PROJECT_ROOT = tmp_path
        _resolve_node_runtime_npm = staticmethod(lambda: '/fake/npm')
        _desktop_build_needed = staticmethod(lambda *a, **k: True)
        _register_desktop_launchers = staticmethod(lambda *a, **k: None)
        @staticmethod
        def _run_logged_subprocess(*args, **kwargs):
            return subprocess.run([sys.executable, '-c', code, str(tmp_path), str(marker)],
                                  capture_output=True, text=True, timeout=5)
    monkeypatch.setattr(update_cmd, '_m', lambda: Main)
    assert update_cmd._rebuild_desktop_after_update(desktop, had_desktop_app_before_update=True)
    assert marker.exists(), 'child could not acquire the workspace lock'
