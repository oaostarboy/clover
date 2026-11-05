"""Capture-to-wait regressions; no process is signalled by the adapter."""
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import time

import pytest
from clover_cli import macos_update_launchd as m


@pytest.fixture
def fake(tmp_path, monkeypatch):
    root = tmp_path / 'install'
    root.mkdir()
    state = {}
    monkeypatch.setattr(m, '_process_table', lambda: state.copy())
    monkeypatch.setattr(m, '_protected_pids', lambda table: {999})
    return root, state, m.LaunchdOperations(roots=(root,))


def proc(pid, ppid, birth, argv=('worker',), cwd=Path('/elsewhere')):
    return m.Process(pid, ppid, birth, argv, cwd, {})


@pytest.mark.parametrize('command', [('worker',), ('node', 'npm', 'run', 'build')])
def test_new_orphan_waits_until_exit(fake, monkeypatch, command):
    root, state, ops = fake
    state[100] = proc(100, 1, 1, ('npm', 'run', 'build'), root)
    captured = ops.capture_builders([])
    state.clear()
    state[101] = proc(101, 1, 2, command, root if command[0] == 'node' else Path('/elsewhere'))
    observations = []
    def sleep(_):
        observations.append(101 in state)
        state.clear()
    monkeypatch.setattr(m.time, 'sleep', sleep)
    ops.wait_quiescent([], captured, 1)
    assert observations[0] is True


def test_unrelated_new_orphan_refuses_without_signals(fake, monkeypatch):
    root, state, ops = fake
    captured = ops.capture_builders([])
    state[101] = proc(101, 1, 2)
    monkeypatch.setattr(m.os, 'kill', lambda *a: pytest.fail('signal forbidden'))
    with pytest.raises(m.LifecycleRefused, match='quiescent'):
        ops.wait_quiescent([], captured, 0)


def test_parent_pid_reuse_refuses(fake):
    root, state, ops = fake
    state[100] = proc(100, 1, 1, ('npm', 'run', 'build'), root)
    captured = ops.capture_builders([])
    state[100] = replace(state[100], birth=2)
    with pytest.raises(m.LifecycleRefused, match='identity'):
        ops.wait_quiescent([], captured, 0)


def test_ambiguous_npm_refuses_but_unrelated_node_allowed(fake):
    root, state, ops = fake
    state[100] = proc(100, 1, 1, ('node', 'other.js'))
    assert ops.capture_builders([]) == []
    state[101] = proc(101, 1, 2, ('npm', 'run', 'build'))
    with pytest.raises(m.LifecycleRefused, match='Ambiguous'):
        ops.capture_builders([])


def test_protected_explicit_builder_refuses(fake):
    root, state, ops = fake
    state[999] = proc(999, 1, 1, ('npm', 'run', 'build'), root)
    with pytest.raises(m.LifecycleRefused, match='ancestor'):
        ops.capture_builders([])


def test_second_empty_observation_catches_orphan(fake, monkeypatch):
    root, state, ops = fake
    captured = ops.capture_builders([])
    calls = []
    def sleep(_):
        calls.append(1)
        if len(calls) == 1:
            state[101] = proc(101, 1, 2)
        else:
            state.clear()
    monkeypatch.setattr(m.time, 'sleep', sleep)
    ops.wait_quiescent([], captured, 1)
    assert len(calls) >= 3


def test_capture_to_mutation_waits_for_new_orphan(fake, monkeypatch):
    from clover_cli.macos_update_lifecycle import quiescent_services
    root, state, ops = fake
    state[100] = proc(100, 1, 1, ('npm', 'run', 'build'), root)
    original_capture = ops.capture_builders
    def capture(services):
        captured = original_capture(services)
        state.clear()
        state[101] = proc(101, 1, 2)
        return captured
    monkeypatch.setattr(ops, 'capture_builders', capture)
    def finish(_):
        state.clear()
    monkeypatch.setattr(m.time, 'sleep', finish)
    mutated = []
    with quiescent_services([], ops, timeout=1):
        assert 101 not in state
        mutated.append(True)
    assert mutated == [True]


def test_new_child_of_tracked_seed_is_waited(fake, monkeypatch):
    root, state, ops = fake
    state[100] = proc(100, 1, 1, ('npm', 'run', 'build'), root)
    captured = ops.capture_builders([])
    state[101] = proc(101, 100, 2)
    seen = []
    def advance(_):
        seen.append(set(state))
        if len(seen) == 1:
            state.pop(100)
            state[101] = replace(state[101], ppid=1)
        else:
            state.clear()
    monkeypatch.setattr(m.time, 'sleep', advance)
    ops.wait_quiescent([], captured, 1)
    assert seen[:2] == [{100, 101}, {101}]


def test_real_detached_child_waits_before_mutation(tmp_path, monkeypatch):
    root = tmp_path / 'install'
    root.mkdir()
    start, ready, finished, pidfile = (tmp_path / n for n in ('start', 'ready', 'finished', 'pid'))
    child_code = ("import pathlib,time; "
                  f"pathlib.Path({str(ready)!r}).touch(); time.sleep(1); "
                  f"pathlib.Path({str(finished)!r}).touch()")
    parent_code = ("import pathlib,subprocess,sys,time; "
                   f"p=pathlib.Path({str(start)!r}); "
                   "exec('while not p.exists():\\n time.sleep(.01)'); "
                   f"c=subprocess.Popen([sys.executable,'-c',{child_code!r}], start_new_session=True); "
                   f"pathlib.Path({str(pidfile)!r}).write_text(str(c.pid))")
    parent = subprocess.Popen([sys.executable, '-c', parent_code], cwd=root)
    real_table = m._process_table
    def isolated_table():
        ids = {parent.pid}
        if pidfile.exists():
            ids.add(int(pidfile.read_text()))
        return {pid: p for pid, p in real_table().items() if pid in ids}
    monkeypatch.setattr(m, '_process_table', isolated_table)
    monkeypatch.setattr(m, '_protected_pids', lambda table: set())
    ops = m.LaunchdOperations(roots=(root,))
    try:
        captured = ops.capture_builders([])
        assert parent.pid in {p.pid for p in captured}
        start.touch()
        assert parent.wait(timeout=5) == 0
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists() and not finished.exists()
        ops.wait_quiescent([], captured, 5)
        assert finished.exists(), 'mutation would start while detached child was alive'
    finally:
        start.touch()
        parent.wait(timeout=5)
