"""Behavior contracts for opt-in proactive check-ins.

Real code against the per-test CLOVER_HOME from conftest. Only the model run
and the chat transport are replaced when driving the scheduler.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import cron.scheduler as s
from clover_cli import config as config_api
from clover_cli.proactive import HELP, proactive_command
from cron import checkin_gate as gate
from cron import checkin_jobs
from cron.jobs import list_jobs

NY = ZoneInfo("America/New_York")


def at(h, m=0, day=10, month=6, tz=NY):
    return datetime(2026, month, day, h, m, tzinfo=tz)


def cfg(**kw):
    base = {"enabled": True, "timezone": "America/New_York"}
    base.update(kw)
    return base


# ── gate ────────────────────────────────────────────────────────────────

def test_gate_enabled_by_default_and_explicit_off_wins():
    assert gate.evaluate({}, at(12), [])[0] is True
    assert gate.evaluate({"enabled": False}, at(12), [])[1] == "disabled"


def test_gate_wakes_inside_hours():
    assert gate.evaluate(cfg(), at(12), [])[0] is True


@pytest.mark.parametrize("h,m,expected", [
    (8, 59, False), (9, 0, True), (19, 59, True), (20, 0, False), (23, 0, False)])
def test_gate_active_hours_start_inclusive_end_exclusive(h, m, expected):
    assert gate.evaluate(cfg(), at(h, m), [])[0] is expected


@pytest.mark.parametrize("h,expected", [(21, False), (22, True), (23, True), (3, True), (5, True), (6, False), (12, False)])
def test_gate_overnight_window(h, expected):
    c = cfg(active_hours={"start": "22:00", "end": "06:00"})
    assert gate.evaluate(c, at(h), [])[0] is expected


def test_gate_paused_until_then_resumes():
    c = cfg(paused_until=at(15).isoformat())
    assert gate.evaluate(c, at(12), [])[0] is False
    assert gate.evaluate(c, at(15, 1), [])[0] is True


def test_gate_daily_cap_and_min_gap():
    c = cfg(max_per_day=2, min_gap_minutes=60)
    assert gate.evaluate(c, at(12), [at(11, 30)])[0] is False  # gap
    assert gate.evaluate(c, at(13), [at(10), at(11)])[1].startswith("daily cap")
    assert gate.evaluate(c, at(13), [at(10)])[0] is True


def test_gate_day_rolls_over_in_users_timezone_not_utc():
    # 22:30 New York on Jun 10 is already Jun 11 in UTC.
    c = cfg(max_per_day=1, active_hours={"start": "00:00", "end": "23:59"}, min_gap_minutes=0)
    sent = [at(21, 0, day=10)]
    assert gate.evaluate(c, at(22, 30, day=10), sent)[0] is False
    assert gate.evaluate(c, at(0, 30, day=11), sent)[0] is True  # new local day


def test_gate_dst_transition_day_uses_wall_clock():
    # US spring-forward 2026-03-08: 02:00 -> 03:00.
    c = cfg(active_hours={"start": "03:00", "end": "20:00"})
    assert gate.evaluate(c, at(2, 30, day=7, month=3), [])[0] is False
    assert gate.evaluate(c, at(3, 0, day=8, month=3), [])[0] is True
    assert gate.evaluate(c, at(19, 59, day=8, month=3), [])[0] is True
    assert gate.evaluate(c, at(20, 0, day=8, month=3), [])[0] is False
    # fall back 2026-11-01: 01:30 happens twice, both before 09:00.
    assert gate.evaluate(cfg(), datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=1), [])[0] is False


def test_gate_bad_settings_fail_closed_not_crash():
    assert gate.evaluate(cfg(active_hours={"start": "nope", "end": "20:00"}), at(12), [])[0] is False


def test_sends_file_counts_only_recorded_sends(tmp_path):
    assert gate.load_sends() == []
    gate.record_send(at(10))
    gate.record_send(at(12))
    assert len(gate.load_sends()) == 2


def test_gate_script_runs_as_scheduler_precheck_and_skips_when_off():
    """The real script, run the way the scheduler runs it, says do not wake."""
    run("off")
    checkin_jobs._write_gate_script()
    ok, out = s._run_job_script(checkin_jobs.GATE_SCRIPT)
    assert ok, out
    assert s._parse_wake_gate(out) is False
    assert "disabled" in out


# ── /proactive checkin ──────────────────────────────────────────────────

def run(args, can_change=True):
    return proactive_command("checkin " + args, can_change=can_change)


def roles():
    return sorted(j.get("checkin_role") for j in list_jobs(include_disabled=True) if j.get("checkin_role"))


def saved():
    return config_api.require_readable_config_before_write().get("proactive", {}).get("checkin", {})


@pytest.fixture
def home(monkeypatch):
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "12345")


def marker():
    from clover_constants import get_clover_home
    return (get_clover_home() / checkin_jobs.SEEDED_MARKER).exists()


def test_fresh_home_defaults_on_and_only_seeding_creates_jobs(home):
    assert roles() == []
    assert config_api.load_config(migrate=False)["proactive"]["checkin"]["enabled"] is True
    config_api.load_config()  # config load/migration never creates jobs
    assert roles() == []
    assert checkin_jobs.seed_default_jobs() is True
    assert roles() == ["brief", "check"]
    assert marker()
    assert checkin_jobs.seed_default_jobs() is False
    assert roles() == ["brief", "check"]
    assert {j["deliver"] for j in list_jobs() if j.get("checkin_role")} == {"telegram"}


def test_migration_does_not_write_enabled_false():
    config_api.load_config()
    config_api.migrate_config(interactive=False, quiet=True)
    raw = config_api.require_readable_config_before_write()
    assert raw.get("proactive", {}).get("checkin", {}).get("enabled", True) is True


def test_old_config_without_checkin_block_is_seeded(home):
    assert checkin_jobs.seed_default_jobs() is True
    assert roles() == ["brief", "check"]


def test_seeding_respects_explicit_off(home):
    run("off")
    assert saved()["enabled"] is False
    assert checkin_jobs.seed_default_jobs() is False
    assert roles() == [] and not marker()


def test_off_then_on_round_trip(home):
    checkin_jobs.seed_default_jobs()
    run("off")
    assert roles() == [] and saved()["enabled"] is False
    run("on")
    assert roles() == ["brief", "check"] and saved()["enabled"] is True


def test_no_home_channel_creates_nothing_then_seeds_once_set(monkeypatch):
    monkeypatch.delenv("TELEGRAM_HOME_CHANNEL", raising=False)
    assert checkin_jobs.seed_default_jobs() is False
    assert roles() == [] and not marker()
    assert "No check-in jobs yet" in run("status")
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "1")
    assert checkin_jobs.seed_default_jobs() is True
    assert roles() == ["brief", "check"]


def test_seeding_no_duplicates_when_user_already_ran_on(home):
    run("on")
    assert roles() == ["brief", "check"]
    assert checkin_jobs.seed_default_jobs() is False
    assert roles() == ["brief", "check"] and marker()


def test_seeding_skips_when_user_has_own_morning_brief_job(home):
    from cron.jobs import create_job
    create_job(prompt="x", schedule="0 7 * * *", name="My morning brief")
    assert checkin_jobs.seed_default_jobs() is False
    assert roles() == [] and marker()


def test_seeding_runs_once_so_deleted_jobs_stay_deleted(home):
    assert checkin_jobs.seed_default_jobs() is True
    checkin_jobs.disable()
    assert checkin_jobs.seed_default_jobs() is False
    assert roles() == []


def test_seeding_is_per_clover_home(home, tmp_path, monkeypatch):
    assert checkin_jobs.seed_default_jobs() is True
    other = tmp_path / "other_home"
    other.mkdir()
    monkeypatch.setenv("CLOVER_HOME", str(other))
    assert not marker()


def test_only_gateway_startup_calls_seeding():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    hits = [str(p.relative_to(root)) for p in root.rglob("*.py")
            if "tests" not in p.parts and ".worktrees" not in p.parts and ".venv" not in p.parts
            and "seed_default_jobs()" in p.read_text(encoding="utf-8", errors="ignore")
            and p.name != "checkin_jobs.py"]
    assert hits == ["gateway/run.py"]


def test_on_creates_exactly_two_jobs_and_is_idempotent():
    reply = run("on")
    assert reply.startswith("Check-ins on. Morning brief 8:00, checks every 3h from 9:00 to 20:00, at most 3 a day.")
    assert roles() == ["brief", "check"]
    first = {j["checkin_role"]: j["id"] for j in list_jobs(include_disabled=True)}
    run("on")
    assert roles() == ["brief", "check"]
    assert {j["checkin_role"]: j["id"] for j in list_jobs(include_disabled=True)} == first


def test_job_shapes():
    run("on")
    by = {j["checkin_role"]: j for j in list_jobs()}
    assert by["brief"]["schedule"]["expr"] == "0 8 * * *"
    assert by["check"]["schedule"]["expr"] == "0 9,12,15,18 * * *"
    assert by["check"]["script"] == checkin_jobs.GATE_SCRIPT
    assert "[SILENT]" in by["check"]["prompt"]
    assert "never send" in by["check"]["prompt"]
    assert not by["brief"].get("script")


def test_off_deletes_both_jobs():
    run("on")
    assert "off" in run("off")
    assert roles() == []
    assert saved()["enabled"] is False


def test_changes_update_existing_jobs_without_duplicates():
    run("on")
    run("brief 07:30")
    run("hours 10-18")
    run("every 2")
    run("max 2")
    assert roles() == ["brief", "check"]
    by = {j["checkin_role"]: j for j in list_jobs()}
    assert by["brief"]["schedule"]["expr"] == "30 7 * * *"
    assert by["check"]["schedule"]["expr"] == "0 10,12,14,16 * * *"
    assert saved()["max_per_day"] == 2
    run("brief off")
    assert roles() == ["check"]


def test_settings_saved_while_off_create_no_jobs():
    run("off")
    assert run("every 4").startswith("Saved.")
    assert roles() == []
    assert saved()["checks_every_hours"] == 4


@pytest.mark.parametrize("args", [
    "brief 25:00", "brief 7:5", "brief soon", "hours 9", "hours 20-20", "hours 9-25",
    "every 0", "every 25", "every x", "max 0", "max 99", "pause 0h", "pause 400d", "pause soon",
    "bogus", "on now"])
def test_invalid_input_rejected_and_nothing_written(args):
    run("on")
    before = (saved(), roles())
    reply = run(args)
    assert not reply.startswith(("Saved", "Check-ins on", "Check-ins paused"))
    assert (saved(), roles()) == before


def test_invalid_input_with_no_prior_config_writes_nothing():
    run("hours 9-99")
    assert "checkin" not in config_api.require_readable_config_before_write().get("proactive", {})


def test_pause_and_resume_keep_jobs():
    run("on")
    assert "paused until" in run("pause 24h")
    assert saved()["paused_until"]
    assert roles() == ["brief", "check"]
    assert run("resume") == "Check-ins resumed."
    assert saved()["paused_until"] == ""


def test_admin_lock_blocks_writes_but_status_reads():
    for args in ("on", "off", "brief 07:00", "hours 9-17", "every 2", "max 1", "pause 1h", "resume"):
        assert "locked" in run(args, can_change=False).lower()
    assert roles() == []
    assert "off" in run("status", can_change=False)
    assert "checkin" not in config_api.require_readable_config_before_write().get("proactive", {})


def test_status_reports_interval_next_run_sends_and_warnings():
    run("on")
    text = run("status")
    assert "checks every 3h" in text
    assert "Next run:" in text
    assert "Sent today: 0 of 3" in text
    assert "no home channel" in text.lower() or "gateway is not running" in text.lower()
    gate.record_send()
    assert "Sent today: 1 of 3" in run("status")


def test_on_warns_about_overlapping_jobs_only_when_they_exist():
    assert "similar job" not in run("on")
    run("off")
    from cron.jobs import create_job
    create_job(prompt="brief me", schedule="0 8 * * *", name="Morning briefing")
    reply = run("on")
    assert reply.count("similar job") == 1
    assert "Morning briefing" in reply


def test_help_and_command_registry_mention_checkin():
    from clover_cli.commands import SUBCOMMANDS
    assert "checkin" in HELP
    assert "checkin" in SUBCOMMANDS["/proactive"]


# ── counting real deliveries ────────────────────────────────────────────

def _fire(monkeypatch, final):
    run("on")
    job = next(j for j in list_jobs() if j["checkin_role"] == "check")
    delivered = []
    monkeypatch.setattr(s, "run_job", lambda job, **kw: (True, "out", final, None))
    monkeypatch.setattr(s, "_deliver_result", lambda job, content, **kw: delivered.append(content) or None)
    monkeypatch.setattr(s, "claim_dispatch", lambda _id: True)
    s.run_one_job(job)
    return delivered


def test_silent_run_delivers_nothing_and_counts_nothing(monkeypatch):
    assert _fire(monkeypatch, "[SILENT]") == []
    assert gate.load_sends() == []


def test_real_message_delivers_and_counts_once(monkeypatch):
    out = _fire(monkeypatch, "Dentist moved to 3pm today. Want me to draft a reply?")
    assert len(out) == 1
    assert len(gate.load_sends()) == 1


def test_brief_delivery_is_not_counted(monkeypatch):
    run("on")
    job = next(j for j in list_jobs() if j["checkin_role"] == "brief")
    monkeypatch.setattr(s, "run_job", lambda job, **kw: (True, "out", "Good morning. 2 meetings.", None))
    monkeypatch.setattr(s, "_deliver_result", lambda job, content, **kw: None)
    monkeypatch.setattr(s, "claim_dispatch", lambda _id: True)
    s.run_one_job(job)
    assert gate.load_sends() == []
