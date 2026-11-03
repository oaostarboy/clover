from types import SimpleNamespace

from tui_gateway import server
from agent import router_backstop as rb


def _hist(text, **extra):
    return [{"role": "user", "content": text, **extra}, {"role": "assistant", "content": "ok"}]


def test_async_delegation_turn_marked_internal_for_old_rows():
    out = server._history_to_messages(_hist("[ASYNC DELEGATION BATCH COMPLETE — deleg_x]\nfinished"))
    assert out[0]["internal"] is True
    assert out[0]["display_kind"] == "internal_notification"


def test_stored_internal_kind_marked_internal():
    out = server._history_to_messages(_hist("anything", display_kind="internal_notification"))
    assert out[0]["internal"] is True


def test_runtime_prefixes_internal_and_real_user_not():
    for t in ("[Clover runtime] note", "[OUT-OF-BAND USER MESSAGE] x"):
        assert server._history_to_messages(_hist(t))[0].get("internal") is True
    real = server._history_to_messages(_hist("hello [ASYNC DELEGATION in the middle"))
    assert "internal" not in real[0]


def test_model_history_untouched():
    h = _hist("[ASYNC DELEGATION COMPLETE — d]")
    server._history_to_messages(h)
    assert h[0]["content"].startswith("[ASYNC")


def test_app_platform_detection_excludes_telegram():
    assert rb._app_platform(SimpleNamespace(platform="mobile"))
    assert rb._app_platform(SimpleNamespace(platform="desktop"))
    assert not rb._app_platform(SimpleNamespace(platform="telegram"))
    assert not rb._app_platform(SimpleNamespace(platform=""))


def _rcpt(summary):
    from agent import foreground_time_limit as fl
    return fl.DispatchReceipt(delegation_id="d1", seq=1, goals=("g",), summary=summary, subagent_ids=())


def test_app_ack_has_no_telegram_card_but_telegram_unchanged():
    summary = "\U0001f504 fixing it \u2192 result, ~5 min"
    tg = rb._ack_text([_rcpt(summary)], SimpleNamespace(platform="telegram"))
    app = rb._ack_text([_rcpt(summary)], SimpleNamespace(platform="mobile"))
    assert tg == rb._ack_text([_rcpt(summary)])  # Telegram path identical to before
    for marker in ("\U0001f500", "\u23f1", "\U0001f6e0", "\u2514", "> "):
        assert marker not in app


def test_non_app_platforms_get_same_ack_as_no_agent():
    # Behaviour contract: only app platforms differ; every other chat
    # platform (Telegram, Discord, Slack, ...) gets the legacy reply.
    summary = "\U0001f504 fixing it \u2192 result, ~5 min"
    legacy = rb._ack_text([_rcpt(summary)])
    for plat in ("telegram", "discord", "slack", "whatsapp", "cli"):
        assert rb._ack_text([_rcpt(summary)], SimpleNamespace(platform=plat)) == legacy
