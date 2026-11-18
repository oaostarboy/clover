"""/stop,/new,/reset cancel the adapter task only after the runner accepted it
(event._interrupt_accepted). Adapted from upstream hermes-agent 90f17a3541."""
import asyncio

import pytest

from tests.gateway.test_command_bypass_active_session import _make_adapter, _make_event, _session_key


async def _install_running_task(adapter, sk):
    adapter._active_sessions[sk] = asyncio.Event()
    task = asyncio.create_task(asyncio.sleep(30))
    adapter._session_tasks[sk] = task
    return task


@pytest.mark.asyncio
async def test_refused_stop_does_not_cancel_running_turn():
    adapter = _make_adapter()
    adapter.STOP_GRACE_S = 0.05
    adapter.set_authorization_check(lambda *a: True)
    sk = _session_key()
    task = await _install_running_task(adapter, sk)
    try:
        await adapter.handle_message(_make_event("/stop"))  # handler never marks accepted
        await asyncio.sleep(0)
        assert not task.cancelled() and not task.done()
        assert adapter._session_tasks.get(sk) is task
        assert adapter._active_sessions.get(sk) is not None
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_accepted_stop_cancels_turn_after_reply():
    adapter = _make_adapter()
    adapter.STOP_GRACE_S = 0.05
    adapter.set_authorization_check(lambda *a: True)

    async def handler(event):
        event._interrupt_accepted = True
        return "stopped"

    adapter._message_handler = handler
    sk = _session_key()
    task = await _install_running_task(adapter, sk)
    await adapter.handle_message(_make_event("/stop"))
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    assert adapter.sent_responses == ["stopped"]


@pytest.mark.asyncio
async def test_no_authorization_check_keeps_legacy_cancel():
    adapter = _make_adapter()
    adapter.STOP_GRACE_S = 0.05
    sk = _session_key()
    task = await _install_running_task(adapter, sk)
    await adapter.handle_message(_make_event("/stop"))
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_accepted_stop_gives_grace_then_cancels():
    adapter = _make_adapter()
    adapter.STOP_GRACE_S = 0.05
    adapter.STOP_GRACE_S = 0.3
    adapter.set_authorization_check(lambda *a: True)

    async def handler(event):
        event._interrupt_accepted = True
        return "stopped"

    adapter._message_handler = handler
    sk = _session_key()
    done = []

    async def finishing():
        await asyncio.sleep(0.1)
        done.append(1)

    adapter._active_sessions[sk] = asyncio.Event()
    t = asyncio.create_task(finishing())
    adapter._session_tasks[sk] = t
    await adapter.handle_message(_make_event("/stop"))
    assert done == [1] and not t.cancelled()  # finished cleanly within grace

    stuck = await _install_running_task(adapter, sk)
    await adapter.handle_message(_make_event("/stop"))
    assert stuck.cancelled() or stuck.done()  # wedged turn cancelled after grace
