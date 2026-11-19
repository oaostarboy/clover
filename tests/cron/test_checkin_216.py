"""v2.1.6 check-ins: 30-minute checks, relevance-based setup tips, the agent's own voice.

Real code against a temp CLOVER_HOME; only the model (AIAgent) is replaced.
"""
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from clover_cli import config as config_api
from clover_cli.proactive import proactive_command
from cron import checkin_gate as gate
from cron import checkin_jobs
from cron import checkin_sources as cs
from cron.jobs import create_job, list_jobs, update_job

OLD_EXPR = "0 9,12,15,18 * * *"  # what 2.1.5 created (3-hour default)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "12345")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def run(args):
    return proactive_command("checkin " + args, can_change=True)


def jobs_by_role():
    return {j["checkin_role"]: j for j in list_jobs(include_disabled=True) if j.get("checkin_role")}


def expr(job):
    return job["schedule"]["expr"]


def minutes_of(e):
    mins, hours = e.split()[:2]
    return sorted(int(h) * 60 + int(m) for h in hours.split(",") for m in mins.split(","))


# ── 30-minute schedule ────────────────────────────────────────────────

def test_default_is_30_minutes_inside_active_hours():
    cfg = gate.merged_settings({})
    assert gate.interval_minutes(cfg) == 30
    e = checkin_jobs.schedules(cfg)["check"]
    times = minutes_of(e)
    assert times[0] == 9 * 60 and times[-1] == 19 * 60 + 30
    assert all(9 * 60 <= t < 20 * 60 for t in times) and len(times) == 22


def test_overnight_window_stays_inside_hours():
    cfg = gate.merged_settings({"active_hours": {"start": "22:00", "end": "02:00"}})
    for t in minutes_of(checkin_jobs.schedules(cfg)["check"]):
        assert t >= 22 * 60 or t < 2 * 60


@pytest.mark.parametrize("arg,minutes", [("30m", 30), ("1h", 60), ("3h", 180), ("15m", 15), ("2", 120)])
def test_every_accepts_minutes_or_hours(arg, minutes):
    run("on")
    assert run("every " + arg).startswith("Saved.")
    assert gate.interval_minutes(gate.merged_settings(
        config_api.require_readable_config_before_write()["proactive"]["checkin"])) == minutes


@pytest.mark.parametrize("arg", ["10m", "5m", "45m", "0", "x", "25h"])
def test_every_rejects_below_15_minutes_and_nonsense(arg):
    run("on")
    before = expr(jobs_by_role()["check"])
    assert run("every " + arg).startswith("Interval looks like")
    assert expr(jobs_by_role()["check"]) == before


def test_status_shows_interval():
    run("on")
    assert "checks every 30m" in run("status")
    run("every 1h")
    assert "checks every 1h" in run("status")


def test_old_hours_key_is_honored_when_non_default():
    assert gate.interval_minutes(gate.merged_settings({"checks_every_hours": 2})) == 120
    # 3h was the old default, so it does not count as a choice
    assert gate.interval_minutes(gate.merged_settings({"checks_every_hours": 3})) == 30
    # an explicit new key wins over the old one
    assert gate.interval_minutes(
        gate.merged_settings({"checks_every_hours": 2, "checks_every_minutes": 45})) == 45


# ── upgrade: Octavia-shaped 2.1.5 jobs ──────────────────────────────────

def _octavia_215_jobs():
    brief = create_job(prompt=checkin_jobs.BRIEF_PROMPT, schedule="0 8 * * *",
                       name=checkin_jobs.BRIEF_NAME, deliver="telegram")
    chk = create_job(prompt=checkin_jobs.CHECK_PROMPT, schedule=OLD_EXPR,
                     name=checkin_jobs.CHECK_NAME, deliver="telegram", script=checkin_jobs.GATE_SCRIPT)
    update_job(brief["id"], {"checkin_role": "brief"})
    update_job(chk["id"], {"checkin_role": "check"})
    return brief["id"], chk["id"]


def test_upgrade_moves_default_interval_job_same_ids_no_duplicates():
    bid, cid = _octavia_215_jobs()
    # 2.1.5 saved checks_every_hours: 3 into config, and marker already exists
    raw = {"checks_every_hours": 3}
    assert checkin_jobs.migrate_default_schedule(raw) is True
    jobs = jobs_by_role()
    assert len(list_jobs(include_disabled=True)) == 2
    assert jobs["brief"]["id"] == bid and jobs["check"]["id"] == cid
    assert len(minutes_of(expr(jobs["check"]))) == 22
    assert expr(jobs["brief"]) == "0 8 * * *"
    assert checkin_jobs.migrate_default_schedule(raw) is False  # idempotent


def test_upgrade_via_gateway_seed_path_moves_schedule(tmp_path):
    from clover_constants import get_clover_home
    _, cid = _octavia_215_jobs()
    (get_clover_home() / checkin_jobs.SEEDED_MARKER).write_text("1")
    assert checkin_jobs.seed_default_jobs() is False
    assert len(minutes_of(expr(jobs_by_role()["check"]))) == 22
    assert jobs_by_role()["check"]["id"] == cid and len(list_jobs(include_disabled=True)) == 2


def test_upgrade_keeps_explicit_user_interval():
    _, cid = _octavia_215_jobs()
    update_job(cid, {"schedule": "0 10,12,14,16 * * *"})  # user chose every 2h under 2.1.5
    assert checkin_jobs.migrate_default_schedule({"checks_every_hours": 2}) is False
    assert expr(jobs_by_role()["check"]) == "0 10,12,14,16 * * *"
    # and an interval saved as explicit minutes is never touched either
    update_job(cid, {"schedule": OLD_EXPR})
    assert checkin_jobs.migrate_default_schedule({"checks_every_minutes": 60}) is True  # 1h -> its own schedule
    assert minutes_of(expr(jobs_by_role()["check"])) == [h * 60 for h in range(9, 20)]


def test_daily_cap_and_gap_defaults_unchanged():
    cfg = gate.merged_settings({})
    assert cfg["max_per_day"] == 3 and cfg["min_gap_minutes"] == 90


# ── gate stays free: gated-out run makes zero model calls ───────────────

def test_gated_out_run_makes_zero_model_calls(monkeypatch):
    from cron.scheduler import run_job
    run("on")
    run("pause 24h")
    job = jobs_by_role()["check"]
    with patch("run_agent.AIAgent") as agent_cls:
        success, _out, _final, _err = run_job(job)
    assert success is True
    agent_cls.assert_not_called()


# ── relevance-based setup suggestions ─────────────────────────────────

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc)
TASKY = [("telegram", t) for t in (
    "can you add this to my todo list", "what tasks are due today?",
    "remind me about the deadline Friday", "hello")]


def _conn(**kw):
    c = {k: False for k in cs.SOURCES}
    c.update(kw)
    return c


def test_tasks_suggested_only_with_task_activity_and_not_connected():
    rel = cs.relevant_items(TASKY)
    assert "tasks" in rel
    base = _conn(mail=True, calendar=True)
    assert cs.next_suggestion(base, {}, NOW, rel) == "tasks"
    assert cs.next_suggestion(base, {}, NOW, cs.relevant_items([("telegram", "hi"), ("cli", "ok")])) is None
    assert cs.next_suggestion(_conn(mail=True, calendar=True, tasks=True), {}, NOW, rel) is None


def test_todoist_in_env_or_mcp_counts_as_connected(monkeypatch, tmp_path):
    assert cs.connected()["tasks"] is False
    monkeypatch.setenv("TODOIST_API_KEY", "x")
    assert cs.connected()["tasks"] is True


def test_task_limits_seven_days_and_two_declines():
    rel = frozenset({"tasks"})
    base = _conn(mail=True, calendar=True)
    st = {"tasks": {"last_suggested": (NOW - timedelta(days=2)).isoformat(), "suggested": 1}}
    assert cs.next_suggestion(base, st, NOW, rel) is None
    st["tasks"]["last_suggested"] = (NOW - timedelta(days=8)).isoformat()
    assert cs.next_suggestion(base, st, NOW, rel) == "tasks"
    assert cs.next_suggestion(base, {"tasks": {"declines": 2}}, NOW, rel) is None


def test_decline_and_stop_recorded(monkeypatch):
    assert "declines=1" in cs.record_decline("tasks")
    monkeypatch.setattr(cs.sys, "argv", ["x", "decline", "tasks", "stop"])
    cs.record_decline("tasks")
    assert cs.load_state()["tasks"]["declines"] == cs.MAX_DECLINES


def test_at_most_one_suggestion_per_message():
    rel = frozenset({"tasks", "voice", "telegram_heavy"})
    assert cs.next_suggestion(_conn(), {}, NOW, rel) == "mail"  # one answer, not a list
    block_lines = []
    for _ in range(1):
        block_lines = cs.facts_block().splitlines()
    assert sum("setup suggestion allowed this run:" in l for l in block_lines) == 1


def test_voice_and_iphone_need_their_own_signal():
    base = _conn(mail=True, calendar=True, tasks=True)
    assert cs.next_suggestion(base, {}, NOW, frozenset()) is None
    voice = [("telegram", '"remind me to call mum"')] * 3
    assert "voice" in cs.relevant_items(voice)
    assert cs.next_suggestion(base, {}, NOW, cs.relevant_items(voice)) == "voice_replies"
    heavy = [("telegram", f"msg {i}") for i in range(20)]
    assert "telegram_heavy" in cs.relevant_items(heavy)
    assert cs.next_suggestion(base, {}, NOW, cs.relevant_items(heavy)) == "iphone"
    cli_only = [("cli", f"msg {i}") for i in range(40)]
    assert cs.next_suggestion(base, {}, NOW, cs.relevant_items(cli_only)) is None


def test_no_second_chat_platform_item():
    assert not any("discord" in i.key or "slack" in i.key or "platform" in i.key for i in cs.CATALOG)


def test_item_without_chat_setup_says_how_not_offers(tmp_path):
    from clover_constants import get_clover_home
    (get_clover_home() / "google_token.json").write_text(json.dumps(
        {"refresh_token": "r", "scopes": ["gmail.readonly", "calendar"]}))
    db = get_clover_home() / "state.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE sessions(id TEXT, source TEXT);"
                    "CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL);")
    c.execute("INSERT INTO sessions VALUES ('s','telegram')")
    for i in range(3):
        c.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES ('s','user',?,?)",
                  ('"quick voice note %d"' % i, time.time()))
    for t in ("add a task for the deadline", "my todo list", "what is due today, any tasks?"):
        c.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES ('s','user',?,?)", (t, time.time()))
    c.commit(); c.close()
    block = cs.facts_block()  # tasks come first and have a chat path
    assert "allowed this run: tasks" in block
    assert cs.load_state()["pending"] == "tasks"
    cs.commit_pending()
    st = cs.load_state()
    st["tasks"]["declines"] = 2
    cs._save_state(st)
    block = cs.facts_block()
    assert "allowed this run: voice_replies (NO chat setup exists" in block
    assert "Do not offer to do it for them" in block


def test_old_messages_and_cron_sessions_do_not_count(tmp_path):
    from clover_constants import get_clover_home
    c = sqlite3.connect(get_clover_home() / "state.db")
    c.executescript("CREATE TABLE sessions(id TEXT, source TEXT);"
                    "CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL);")
    c.execute("INSERT INTO sessions VALUES ('old','telegram'),('cr','cron')")
    for i in range(5):
        c.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES ('old','user','my tasks',?)",
                  (time.time() - 20 * 86400,))
        c.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES ('cr','user','my tasks',?)",
                  (time.time(),))
    c.commit(); c.close()
    assert cs.relevant_items() == frozenset()


# ── the agent's own voice ────────────────────────────────────────────

def test_prompts_do_not_hardcode_a_tone():
    for p in (checkin_jobs.BRIEF_PROMPT, checkin_jobs.CHECK_PROMPT):
        assert "as yourself, in your usual voice" in p
        assert "pirate" not in p.lower() and "cheerful" not in p.lower()


def _build_agent_system_prompt(job, home, monkeypatch):
    """Run run_job for real up to AIAgent construction and build its system prompt."""
    import cron.scheduler as sch
    from run_agent import AIAgent
    captured = {}

    class Boom(Exception):
        pass

    def fake(*a, **kw):
        captured.update(kw)
        raise Boom()

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("CLOVER_MODEL", "test/model")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    with patch("run_agent.AIAgent", side_effect=fake), \
         patch("cron.scheduler._run_job_script_with_claim_heartbeat", return_value=(True, '{"wakeAgent": true}')):
        sch.run_job(job)
    assert captured, "agent was never constructed"
    kw = {k: v for k, v in captured.items() if k in AIAgent.__init__.__code__.co_varnames}
    return captured


def test_checkin_agent_gets_voice_overlay_and_soul_user_memory(tmp_path, monkeypatch):
    from clover_constants import get_clover_home
    from agent.system_prompt import build_system_prompt
    home = get_clover_home()
    (home / "SOUL.md").write_text("You talk like a pirate. SOUL-MARKER-ARR\n")
    (home / "memories").mkdir(exist_ok=True)
    (home / "memories" / "USER.md").write_text("The user is called Captain Ant. USER-MARKER-Q\n")
    (home / "memories" / "MEMORY.md").write_text("Prefers short replies. MEMORY-MARKER-Z\n")
    config_api.set_config_value("agent.system_prompt", "Keep it salty. OVERLAY-MARKER-SALT")
    run("on")
    job = jobs_by_role()["check"]
    kw = _build_agent_system_prompt(job, home, monkeypatch)
    assert kw["load_soul_identity"] is True and kw["skip_memory"] is False
    assert "OVERLAY-MARKER-SALT" in kw["ephemeral_system_prompt"]

    from run_agent import AIAgent
    real = AIAgent(model="x", api_key="k", base_url="http://127.0.0.1:1/v1", quiet_mode=True,
                   skip_context_files=kw["skip_context_files"], load_soul_identity=True,
                   skip_memory=False, platform="cron", enabled_toolsets=["memory"])
    sp = build_system_prompt(real)
    assert "SOUL-MARKER-ARR" in sp and "USER-MARKER-Q" in sp and "MEMORY-MARKER-Z" in sp


def test_non_checkin_cron_job_prompt_unchanged(monkeypatch):
    import cron.scheduler as sch
    config_api.set_config_value("agent.system_prompt", "Keep it salty. OVERLAY-MARKER-SALT")
    assert sch._checkin_voice_prompt({"id": "x", "prompt": "hi", "deliver": "telegram"}, {}) is None
    assert sch._checkin_voice_prompt({"id": "x", "checkin_role": "check", "deliver": "telegram"},
                                     config_api.load_config()) is not None


def test_checkin_gets_delivery_platform_hint():
    import cron.scheduler as sch
    from agent.prompt_builder import PLATFORM_HINTS
    out = sch._checkin_voice_prompt({"checkin_role": "brief", "deliver": "telegram:123"}, {})
    assert PLATFORM_HINTS["telegram"] in out
