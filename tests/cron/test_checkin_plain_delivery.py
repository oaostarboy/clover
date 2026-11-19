"""Proactive check-ins arrive as plain chat messages, not cron cards."""
from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cron import scheduler as sch

BODY = "Morning. Two things today:\n- Baptiste needs a reply\n- 3pm call"
CARD_CFG = {"cron": {"wrap_response": True, "style": "card"}}


def _deliver(job, content, cfg):
    from gateway.config import Platform
    adapter = MagicMock()
    adapter.send = AsyncMock(return_value=MagicMock(success=True, message_id="1"))
    pconfig = MagicMock()
    pconfig.enabled = True
    pconfig.extra = {}
    gcfg = MagicMock()
    gcfg.platforms = {Platform.TELEGRAM: pconfig}
    gcfg.get_home_channel.return_value = None
    loop = MagicMock()
    loop.is_running.return_value = True

    def run(coro, _loop):
        import asyncio
        f = Future()
        try:
            f.set_result(asyncio.run(coro))
        except BaseException as e:  # noqa: BLE001
            f.set_exception(e)
        return f

    with patch("gateway.config.load_gateway_config", return_value=gcfg), \
         patch("cron.scheduler.load_config", return_value=cfg), \
         patch("asyncio.run_coroutine_threadsafe", side_effect=run), \
         patch("cron.scheduler._maybe_mirror_cron_delivery") as mirror:
        sch._deliver_result(job, content, adapters={Platform.TELEGRAM: adapter}, loop=loop)
    sent = [c[0][1] for c in adapter.send.call_args_list]
    return sent, mirror


def _job(**kw):
    j = {"id": "de643aa3a34e", "name": "Proactive morning brief",
         "deliver": "origin", "origin": {"platform": "telegram", "chat_id": "42"}}
    j.update(kw)
    return j


@pytest.mark.parametrize("role", ["brief", "check"])
@pytest.mark.parametrize("cfg", [CARD_CFG, {"cron": {"wrap_response": True, "style": "classic"}},
                                 {"cron": {"wrap_response": False}}])
def test_checkin_is_clean_and_mirrored(role, cfg):
    sent, mirror = _deliver(_job(checkin_role=role), BODY, cfg)
    assert sent == [BODY]
    assert mirror.call_args.kwargs["enabled"] is True


def test_non_checkin_still_gets_card_and_no_mirror():
    sent, mirror = _deliver(_job(), BODY, CARD_CFG)
    assert len(sent) == 1 and sent[0] != BODY
    assert "job_id: de643aa3a34e" in sent[0] and BODY.split("\n")[0] in sent[0]
    assert mirror.call_args.kwargs["enabled"] is False


def test_format_function_non_checkin_unchanged_classic():
    out = sch._format_cron_delivery_text(
        {"id": "x", "name": "N"}, "hi", {"cron": {"style": "classic"}}, True)
    assert out.startswith("Cronjob Response: N\n(job_id: x)")


def test_mirror_gate():
    assert sch._cron_mirror_delivery_enabled({"checkin_role": "brief"}, {}) is True
    assert sch._cron_mirror_delivery_enabled({"checkin_role": "check", "attach_to_session": False}, {}) is False
    assert sch._cron_mirror_delivery_enabled({"id": "o"}, {}) is False


def test_checkin_mirror_is_user_role_labelled():
    with patch("gateway.mirror.mirror_to_session", return_value=True) as m:
        sch._maybe_mirror_cron_delivery(
            {"id": "a", "checkin_role": "brief"}, "telegram", "42", BODY, enabled=True)
    assert m.call_args.kwargs["role"] == "user"
    assert m.call_args.args[2].endswith(BODY)
    assert "job_id" not in m.call_args.args[2]


def test_silent_is_recognised_before_delivery():
    # run_job skips _deliver_result (and so the mirror) for silent output.
    assert sch._is_cron_silence_response("[SILENT]")
