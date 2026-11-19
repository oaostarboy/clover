"""Check-ins offer to do things, suggest setup from real facts, never act."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from cron import checkin_jobs, checkin_sources as cs


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path


def _token(home, *scopes):
    (home / "google_token.json").write_text(json.dumps(
        {"refresh_token": "r", "scopes": [f"https://www.googleapis.com/auth/{s}" for s in scopes]}))


def test_facts_none_connected(home):
    assert cs.connected() == {"mail": False, "calendar": False}
    block = cs.facts_block()
    assert "mail: NOT connected" in block and "calendar: NOT connected" in block
    assert "setup suggestion allowed this run: mail" in block


def test_facts_mail_only(home):
    _token(home, "gmail.readonly")
    assert cs.connected() == {"mail": True, "calendar": False}
    assert "setup suggestion allowed this run: calendar" in cs.facts_block()


def test_facts_all_connected(home):
    _token(home, "gmail.readonly", "calendar")
    assert cs.connected() == {"mail": True, "calendar": True}
    assert "allowed this run: none" in cs.facts_block()


def test_himalaya_config_counts_as_mail(home):
    cfg = home / "xdg" / "himalaya"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text('[accounts.work]\nemail = "a@b.c"\n')
    assert cs.connected()["mail"] is True


NOW = datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc)
NONE = {"mail": False, "calendar": False}


def test_once_per_seven_days_per_source(home):
    st = {"mail": {"last_suggested": (NOW - timedelta(days=2)).isoformat(), "suggested": 1}}
    assert cs.next_suggestion(NONE, st, NOW) == "calendar"
    st["calendar"] = {"last_suggested": (NOW - timedelta(days=1)).isoformat(), "suggested": 1}
    assert cs.next_suggestion(NONE, st, NOW) is None
    st["mail"]["last_suggested"] = (NOW - timedelta(days=8)).isoformat()
    assert cs.next_suggestion(NONE, st, NOW) == "mail"


def test_two_declines_stop_that_source(home):
    st = {"mail": {"declines": 2}}
    assert cs.next_suggestion(NONE, st, NOW) == "calendar"
    cs.record_decline("calendar")
    assert cs.load_state()["calendar"]["declines"] == 1
    cs.record_decline("calendar")
    assert cs.next_suggestion(NONE, cs.load_state(), NOW) is None or \
        cs.next_suggestion(NONE, cs.load_state(), NOW) == "mail"
    assert cs.load_state()["calendar"]["declines"] == 2


def test_not_repeated_back_to_back_and_only_counted_when_delivered(home):
    cs.facts_block()                       # run 1 picks "mail" but nothing delivered yet
    assert cs.load_state().get("mail", {}).get("suggested", 0) == 0
    cs.commit_pending()                    # delivered
    assert cs.load_state()["mail"]["suggested"] == 1
    assert "allowed this run: calendar" in cs.facts_block()   # next run: not mail again


def test_connected_source_never_suggested(home):
    _token(home, "gmail.readonly", "calendar")
    assert cs.next_suggestion(cs.connected(), {}, NOW) is None


def test_prompts_offer_and_ask_for_yes_and_forbid_acting():
    for p in (checkin_jobs.BRIEF_PROMPT, checkin_jobs.CHECK_PROMPT):
        assert "Want me to?" in p and "yes" in p
        assert "READ ONLY" in p and "never send, book, move, delete" in p
        assert "Connected sources" in p


def test_old_jobs_get_new_prompt_edited_ones_do_not():
    old = checkin_jobs.LEGACY_PROMPTS
    assert len(old) == 2
    for p in old:
        assert p not in (checkin_jobs.BRIEF_PROMPT, checkin_jobs.CHECK_PROMPT)
    brief_old = next(p for p in old if "morning brief" in p)
    assert checkin_jobs.current_prompt({"prompt": brief_old, "checkin_role": "brief"}) == checkin_jobs.BRIEF_PROMPT
    assert checkin_jobs.current_prompt({"prompt": "mine", "checkin_role": "brief"}) == "mine"


def test_run_prompt_contains_facts(home):
    from cron.scheduler import _build_job_prompt
    job = {"id": "x", "name": "n", "prompt": checkin_jobs.CHECK_PROMPT, "checkin_role": "check"}
    out = _build_job_prompt(job)
    assert "mail: NOT connected" in out
    plain = _build_job_prompt({"id": "y", "name": "n", "prompt": "hello"})
    assert "Connected sources" not in plain


def test_check_run_cannot_send_or_schedule():
    """How it really works: cron agents always have messaging/clarify/cronjob
    toolsets disabled, and the prompt says READ ONLY. Mail/calendar writes
    would need an explicit user yes in the normal chat."""
    from cron.scheduler import _resolve_cron_disabled_toolsets
    off = _resolve_cron_disabled_toolsets({})
    assert {"messaging", "clarify", "cronjob"} <= set(off)


def test_yes_reply_sees_mirrored_offer(home):
    """The offer is mirrored into the chat session, so a 'yes' has context."""
    from clover_state import SessionDB
    from gateway.mirror import mirror_to_session
    from cron.scheduler import _maybe_mirror_cron_delivery

    db = SessionDB()
    db.create_session("sess1", "telegram", chat_id="42", session_key="agent:main:telegram:dm",
                      user_id="u1")
    offer = "Daniel is waiting on your Aryeo decision. I can reply and book Thursday. Want me to?"
    _maybe_mirror_cron_delivery(
        {"id": "a", "name": "Proactive check-in", "checkin_role": "check"},
        "telegram", "42", offer, enabled=True)
    msgs = SessionDB().get_messages_as_conversation("sess1")
    joined = "\n".join(str(m.get("content")) for m in msgs)
    assert offer in joined
    assert msgs[-1]["role"] == "user"
