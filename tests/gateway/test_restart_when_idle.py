"""Restart waits for running turns AND background subagents (2.1 lane 9)."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from gateway import restart_when_idle as rwi
from tests.gateway.restart_test_helpers import make_restart_runner


def _runner(monkeypatch, units):
    runner, _ = make_restart_runner()
    runner._restart_after_turn_timeout = 30.0
    runner._async_notices_in_delivery = []
    runner._launch_detached_restart_command = AsyncMock()
    runner.stop = AsyncMock()
    monkeypatch.setattr(rwi, "background_units", lambda r: list(units))
    return runner


@pytest.mark.asyncio
async def test_restart_waits_for_background_subagent_then_stops(monkeypatch):
    units = [{"kind": "delegation", "delegation_id": "d1"}]
    runner = _runner(monkeypatch, units)
    assert runner.request_restart(detached=False, via_service=True)
    await asyncio.sleep(0.4)
    runner.stop.assert_not_called()          # subagent still live
    assert runner._draining is False         # still serving / reports can land
    units.clear()                            # child reported
    await asyncio.wait_for(runner._restart_task, 5)
    runner.stop.assert_awaited_once()
    assert runner._draining is True


@pytest.mark.asyncio
async def test_restart_waits_for_running_turn(monkeypatch):
    runner = _runner(monkeypatch, [])
    runner._running_agents["k"] = object()
    runner._active_work_count = lambda: len(runner._running_agents)
    runner._wedged_agent_count = lambda: 0
    runner.request_restart(detached=False, via_service=True)
    await asyncio.sleep(0.4)
    runner.stop.assert_not_called()
    runner._running_agents.clear()
    await asyncio.wait_for(runner._restart_task, 5)
    runner.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_wait_is_bounded_by_config_cap(monkeypatch):
    runner = _runner(monkeypatch, [{"kind": "delegation", "delegation_id": "d1"}])
    runner._restart_after_turn_timeout = 0.3
    runner.request_restart(detached=False, via_service=True)
    await asyncio.wait_for(runner._restart_task, 5)
    runner.stop.assert_awaited_once()


def test_background_units_counts_only_live_delegations_and_notices(monkeypatch):
    from tools import async_delegation as ad
    monkeypatch.setattr(ad, "list_async_delegations", lambda: [
        {"delegation_id": "a", "status": "running"},
        {"delegation_id": "b", "status": "completed"},
    ])
    runner, _ = make_restart_runner()
    runner._async_notices_in_delivery = [{"type": "async_delegation", "delegation_id": "c"}]
    units = rwi.background_units(runner)
    assert [u.get("delegation_id") or u.get("id") for u in units] == ["a", "c"]


def test_background_units_counts_notify_watched_processes(monkeypatch):
    from types import SimpleNamespace
    import threading
    from tools import async_delegation as ad
    from tools.process_registry import process_registry as reg
    monkeypatch.setattr(ad, "list_async_delegations", lambda: [])
    mk = lambda i, n: SimpleNamespace(id=i, notify_on_complete=n, _completion_event=threading.Event())
    monkeypatch.setattr(reg, "_running", {"p1": mk("p1", True), "p2": mk("p2", False)})
    runner, _ = make_restart_runner()
    runner._async_notices_in_delivery = []
    assert [u["id"] for u in rwi.background_units(runner)] == ["p1"]


@pytest.mark.asyncio
async def test_restart_announces_waiting_for_idle_to_requesting_chat(monkeypatch):
    from types import SimpleNamespace
    from gateway.config import Platform
    units = [{"kind": "delegation", "delegation_id": "d1"}]
    runner = _runner(monkeypatch, units)
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._restart_command_source = SimpleNamespace(
        platform=Platform.TELEGRAM, chat_id="42", thread_id=None, message_id=None, chat_type="dm")
    assert runner.request_restart(detached=False, via_service=True)
    await asyncio.sleep(0.4)
    units.clear()
    await asyncio.wait_for(runner._restart_task, 5)
    adapter.send.assert_awaited_once()
    assert "Waiting for idle" in adapter.send.await_args.args[1]
