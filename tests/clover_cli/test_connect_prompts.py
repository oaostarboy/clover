"""Connect flows: plugin platforms ask for settings instead of 'edit .env'."""
from unittest.mock import patch

import clover_cli.gateway as gw


def _run(names, answers, key=""):
    saved, asked = {}, []

    def fake_prompt(q, default="", password=False, **_):
        asked.append((q, password))
        return answers.pop(0)

    with patch.object(gw, "prompt", fake_prompt), \
         patch.object(gw, "get_env_value", lambda n: None), \
         patch.object(gw, "save_env_value", lambda n, v: saved.__setitem__(n, v)):
        ok = gw._prompt_required_env(names, key)
    return ok, saved, asked


def test_saves_each_value_and_masks_secrets():
    ok, saved, asked = _run(["EMAIL_ADDRESS", "EMAIL_PASSWORD"], ["a@b.c", " pw "])
    assert ok and saved == {"EMAIL_ADDRESS": "a@b.c", "EMAIL_PASSWORD": "pw"}
    assert dict(asked)["  Enter email password"] is True
    assert dict(asked)["  Enter email address"] is False


def test_blank_answer_stops_and_reports_unfinished():
    ok, saved, _ = _run(["A_HOST", "B_HOST"], ["x", ""])
    assert not ok and saved == {"A_HOST": "x"}


def test_email_and_sms_also_ask_for_the_settings_they_need_to_start():
    _, saved, _ = _run(["EMAIL_ADDRESS"], ["a", "imap.x"], "email")
    assert "EMAIL_IMAP_HOST" in saved
    _, saved, _ = _run(["TWILIO_AUTH_TOKEN"], ["t", "https://x.example/webhooks/twilio"], "sms")
    assert "SMS_WEBHOOK_URL" in saved
