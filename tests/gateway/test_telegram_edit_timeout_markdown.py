"""A timed-out Telegram edit must never post raw markdown as plain text."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram import adapter as tg
from plugins.platforms.telegram.adapter import TelegramAdapter, _strip_mdv2

CARD = "**> 🛠 7 tool calls · ⏱ 1m43s||"


class TimedOut(Exception):
    pass


class BadRequest(Exception):
    pass


def _adapter(side_effect):
    a = TelegramAdapter(PlatformConfig(enabled=True, token="t"))
    a._rich_send_disabled = True
    bot = MagicMock()
    bot.edit_message_text = AsyncMock(side_effect=side_effect)
    a._bot = bot
    return a, bot


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(tg, "_EDIT_RETRY_DELAYS", (0.0, 0.0, 0.0))


def _parse_modes(bot):
    return [c.kwargs.get("parse_mode") for c in bot.edit_message_text.await_args_list]


@pytest.mark.asyncio
async def test_timeout_retries_same_markdownv2_edit_then_succeeds():
    a, bot = _adapter([TimedOut("Timed out"), TimedOut("Timed out"), MagicMock()])
    res = await a.edit_message("1", "5", CARD, finalize=True)
    assert res.success
    assert bot.edit_message_text.await_count == 3
    assert all(m == tg.ParseMode.MARKDOWN_V2 for m in _parse_modes(bot))
    texts = {c.kwargs["text"] for c in bot.edit_message_text.await_args_list}
    assert len(texts) == 1  # same text every time, never a plain-text rewrite


@pytest.mark.asyncio
async def test_timeout_that_landed_counts_as_not_modified_success():
    a, bot = _adapter([TimedOut("Timed out"), BadRequest("Message is not modified")])
    assert (await a.edit_message("1", "5", CARD, finalize=True)).success


@pytest.mark.asyncio
async def test_persistent_timeout_leaves_previous_message_alone():
    a, bot = _adapter(TimedOut("Timed out"))
    res = await a.edit_message("1", "5", CARD, finalize=True)
    assert res.success is False
    assert None not in _parse_modes(bot)          # no plain-text overwrite attempt
    assert all(m == tg.ParseMode.MARKDOWN_V2 for m in _parse_modes(bot))


@pytest.mark.asyncio
async def test_real_parse_error_falls_back_to_clean_plain_text():
    a, bot = _adapter([BadRequest("Can't parse entities: character '|' is reserved"), MagicMock()])
    res = await a.edit_message("1", "5", CARD, finalize=True)
    assert res.success
    plain = bot.edit_message_text.await_args_list[-1].kwargs
    assert plain.get("parse_mode") is None
    assert plain["text"] == "🛠 7 tool calls · ⏱ 1m43s"


def test_plain_fallback_strips_all_markers():
    assert _strip_mdv2(CARD) == "🛠 7 tool calls · ⏱ 1m43s"
    out = _strip_mdv2("**bold** __under__ ~gone~ ||spoiler|| \\.\n> quoted\n**> exp\nline||")
    for raw in ("**", "__", "||", "\\", "> ", "~"):
        assert raw not in out
    assert "bold" in out and "quoted" in out and "exp" in out
    assert "snake_case_name" in _strip_mdv2("snake_case_name")
