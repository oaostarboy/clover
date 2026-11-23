"""An album must stay one message even when a member's download is slow.

Telegram delivers album photos as separate updates processed one at a time.
The album debounce timer used to keep running while the next member was still
downloading, so a download slower than the debounce window split the album
into two user turns (seen live: 5-photo album -> 1 + 4).
Uses real PTB Update/Message/PhotoSize/File; only the Bot API is offline.
"""
from __future__ import annotations

import asyncio

import pytest
from telegram import Bot

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter
from tests.test_telegram_album_queue_ownership import OfflineTelegram, photo


class SlowFileTelegram(OfflineTelegram):
    def __init__(self, slow, delay):
        super().__init__()
        self.slow, self.delay = set(slow), delay

    async def do_request(self, url, method, request_data=None, **kwargs):
        if '/file/bot' in url:
            number = int(url.rsplit('/', 1)[-1].split('.')[0])
            if number in self.slow:
                await asyncio.sleep(self.delay)
        return await super().do_request(url, method, request_data, **kwargs)


def adapter(monkeypatch, tmp_path):
    monkeypatch.setattr('gateway.platforms.base.IMAGE_CACHE_DIR', tmp_path / 'images')
    a = TelegramAdapter(PlatformConfig(enabled=True, token='123:offline'))
    a._is_user_authorized_from_message = lambda msg: True
    a._should_process_message = lambda msg: True
    a.MEDIA_GROUP_WAIT_SECONDS = .05
    a._media_batch_delay_seconds = .05
    events = []

    async def handle(event):
        events.append(event)

    a.handle_message = handle
    return a, events


@pytest.mark.asyncio
async def test_slow_member_download_does_not_split_album(monkeypatch, tmp_path):
    a, events = adapter(monkeypatch, tmp_path)
    # member 2 download takes 4x the debounce window
    bot = Bot('123:offline', request=SlowFileTelegram(slow={31}, delay=.2))
    for number in [30, 31, 32, 33, 34]:
        await a._handle_media_message(
            photo(bot, number, 'look' if number == 30 else None, album='five'), None)
    await asyncio.sleep(.2)
    assert len(events) == 1
    assert len(events[0].media_urls) == 5
    assert events[0].text == 'look'
    assert not getattr(a, '_media_group_inflight', {})


@pytest.mark.asyncio
async def test_fast_album_still_one_message_and_separate_albums_stay_separate(monkeypatch, tmp_path):
    a, events = adapter(monkeypatch, tmp_path)
    bot = Bot('123:offline', request=SlowFileTelegram(slow=(), delay=0))
    for number in [40, 41]:
        await a._handle_media_message(photo(bot, number, album='a'), None)
    await asyncio.sleep(.15)
    for number in [50, 51, 52]:
        await a._handle_media_message(photo(bot, number, album='b'), None)
    await asyncio.sleep(.15)
    assert [len(e.media_urls) for e in events] == [2, 3]


@pytest.mark.asyncio
async def test_failed_member_download_still_flushes_album(monkeypatch, tmp_path):
    a, events = adapter(monkeypatch, tmp_path)

    class Broken(SlowFileTelegram):
        async def do_request(self, url, method, request_data=None, **kwargs):
            if '/file/bot' in url and url.endswith('/61.jpg'):
                raise RuntimeError('boom')
            return await super().do_request(url, method, request_data, **kwargs)

    bot = Bot('123:offline', request=Broken(slow=(), delay=0))
    a._surface_media_cache_failure = lambda *args, **kw: asyncio.sleep(0)
    for number in [60, 61, 62]:
        await a._handle_media_message(photo(bot, number, album='c'), None)
    await asyncio.sleep(.2)
    assert len(events) == 1
    assert len(events[0].media_urls) == 2
    assert not getattr(a, '_media_group_inflight', {})
