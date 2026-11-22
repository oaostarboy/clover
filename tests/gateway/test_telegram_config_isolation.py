"""Per-bot Telegram settings: top-level bridge, env isolation, precedence."""
import os
from types import SimpleNamespace

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram import adapter as tg


def _adapter(extra):
    a = object.__new__(tg.TelegramAdapter)
    a.config = PlatformConfig(enabled=True, token="x", extra=extra or {})
    return a


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("TELEGRAM_REACTIONS", "TELEGRAM_REQUIRE_MENTION"):
        monkeypatch.delenv(k, raising=False)


def test_top_level_ignore_root_dm_and_group_topics_reach_extra():
    extras = tg._apply_yaml_config({}, {"ignore_root_dm": True, "group_topics": [{"chat_id": 1}]})
    assert extras["ignore_root_dm"] is True
    assert extras["group_topics"] == [{"chat_id": 1}]


def test_explicit_extra_wins_over_top_level():
    extras = tg._apply_yaml_config(
        {}, {"ignore_root_dm": True, "extra": {"ignore_root_dm": False}}
    )
    assert extras["ignore_root_dm"] is False


@pytest.mark.parametrize("order", [(True, False), (False, True)])
def test_reactions_do_not_leak_between_bots(order):
    extras = [tg._apply_yaml_config({}, {"reactions": v}) for v in order]
    assert "TELEGRAM_REACTIONS" not in os.environ
    adapters = [_adapter(e) for e in extras]
    assert [a._reactions_enabled() for a in adapters] == list(order)


@pytest.mark.parametrize("order", [(True, False), (False, True)])
def test_require_mention_does_not_leak_between_bots(order):
    for v in order:
        tg._apply_yaml_config({}, {"require_mention": v})
    assert "TELEGRAM_REQUIRE_MENTION" not in os.environ
    a, b = (_adapter({"require_mention": v}) for v in order)
    assert (a._telegram_require_mention(), b._telegram_require_mention()) == order


def test_external_env_overrides_reactions(monkeypatch):
    monkeypatch.setenv("TELEGRAM_REACTIONS", "true")
    extras = tg._apply_yaml_config({}, {"reactions": False})
    assert _adapter(extras)._reactions_enabled() is True
    assert os.environ["TELEGRAM_REACTIONS"] == "true"


def test_external_env_require_mention_fallback(monkeypatch):
    monkeypatch.setenv("TELEGRAM_REQUIRE_MENTION", "true")
    assert _adapter({})._telegram_require_mention() is True
