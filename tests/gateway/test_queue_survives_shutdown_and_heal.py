"""Adapter overflow/debounce queues must survive stale-lock healing and shutdown."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    TextDebounceState,
)
from gateway.session import SessionSource, build_session_key


class _Stub(BasePlatformAdapter):
    async def connect(self, *, is_reconnect: bool = False):
        pass

    async def disconnect(self):
        pass

    async def send(self, chat_id, text, **kwargs):
        return None

    async def get_chat_info(self, chat_id):
        return {}


def _adapter():
    a = _Stub(PlatformConfig(enabled=True, token="t"), Platform.TELEGRAM)
    a._send_with_retry = AsyncMock(return_value=None)
    return a


def _src():
    return SessionSource(platform=Platform.TELEGRAM, chat_id="42", chat_type="dm")


def _ev(text):
    return MessageEvent(text=text, message_type=MessageType.TEXT, source=_src())


def _done_task():
    t = MagicMock()
    t.done.return_value = True
    return t


def test_heal_keeps_queued_events_in_order():
    a = _adapter()
    sk = build_session_key(_src())
    a._active_sessions[sk] = asyncio.Event()
    a._session_tasks[sk] = _done_task()
    a._pending_messages[sk] = _ev("head")
    a._local_overflow_store()[sk] = [_ev("o1"), _ev("o2")]
    a._text_debounce_store()[sk] = TextDebounceState(
        event=_ev("held"), task=None, first_ts=0.0, last_ts=0.0
    )
    assert a._heal_stale_session_lock(sk) is True
    texts = [e.text for e in a._take_healed_orphans(sk)]
    assert texts == ["head", "o1", "o2", "held"]


@pytest.mark.asyncio
async def test_handle_message_after_heal_redelivers_orphans_before_new_event():
    a = _adapter()
    sk = build_session_key(_src())
    processed = []

    async def handler(event):
        processed.append(event.text)
        return None

    a.set_message_handler(handler)
    a._active_sessions[sk] = asyncio.Event()
    a._session_tasks[sk] = _done_task()
    a._pending_messages[sk] = _ev("head")
    a._local_overflow_store()[sk] = [_ev("o1")]
    await a.handle_message(_ev("new"))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not a._background_tasks and not a._pending_messages:
            break
    assert processed[0] == "head"
    joined = "\n".join(processed)
    assert "o1" in joined and "new" in joined


@pytest.mark.asyncio
async def test_adapter_shutdown_spools_overflow_and_debounce(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path))
    a = _adapter()
    sk = build_session_key(_src())
    a._pending_messages[sk] = _ev("head")
    a._local_overflow_store()[sk] = [_ev("o1"), _ev("o2")]
    a._text_debounce_store()[sk] = TextDebounceState(
        event=_ev("held"), task=None, first_ts=0.0, last_ts=0.0
    )
    await a.cancel_background_tasks()
    files = sorted((tmp_path / "pending_messages").glob("pending-*.json"))
    items = [json.loads(f.read_text()) for f in files]
    items.sort(key=lambda p: (p["ts"], p.get("seq", 0)))
    assert [p["data"]["text"] for p in items] == ["head", "o1", "o2", "held"]
    assert all(p["session_key"] == sk for p in items)
