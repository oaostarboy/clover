"""Create, update and remove the two ordinary cron jobs behind proactive check-ins.

``/proactive checkin on`` calls ``enable``. Check-ins are on by default, so
gateway startup calls ``seed_default_jobs`` once per profile; an explicit
``off`` (saved ``enabled: false``) is never overridden.
Jobs are tagged ``checkin_role`` (``brief`` or ``check``) so they can be found.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping, Optional

from cron import checkin_gate as gate

logger = logging.getLogger(__name__)

TAG = "checkin_role"
BRIEF_NAME = "Proactive morning brief"
CHECK_NAME = "Proactive check-in"
GATE_SCRIPT = "proactive_checkin_gate.py"

BRIEF_PROMPT = (
    "Write a short morning brief for the user. Use whichever of these this "
    "profile actually has connected, and silently skip any that are missing: "
    "today's calendar events, mail that needs a reply, tasks due today, and "
    "open workstreams. If a google-workspace skill is installed, load it and follow "
    "its references/daily-brief.md. Highlight conflicts and anything time-critical. "
    "Keep it to a few lines. READ ONLY: never send, book, move, delete or change "
    "anything during this run. When you find something you could handle with your "
    "own tools (reply to an email, book or move an event, file a task, finish open "
    "work), say in one line what you would do and ask for a yes, for example "
    "\"Daniel is waiting on your Aryeo decision. I can reply and book Thursday. "
    "Want me to?\" The user's yes arrives in the normal chat, where you then do it. "
    "Use the 'Connected sources' facts below as the truth about what is connected. "
    "If one setup suggestion is allowed, you may add ONE short line offering to "
    "help connect that source right here in the chat, naming the real setup it "
    "uses; never suggest anything else. If nothing is connected, give a one-line "
    "good-morning with the date plus the allowed setup suggestion if any."
)

CHECK_PROMPT = (
    "Quietly check, using only the mail, calendar, tasks and open workstreams "
    "this profile has connected (skip missing ones silently), what changed "
    "since the last run. READ ONLY: never send, book, move, delete or change "
    "anything during this run. Message ONLY for something the user must act on "
    "today or that is time-critical. If you do, give the reason in one line, and "
    "if you could handle it with your own tools (reply, book or move an event, "
    "file a task, finish open work) say in one line what you would do and ask for "
    "a yes, for example \"Daniel is waiting on your Aryeo decision. I can reply "
    "and book Thursday. Want me to?\" The user's yes arrives in the normal chat, "
    "where you then do it. Use the 'Connected sources' facts below as the truth "
    "about what is connected. If nothing is pressing but a setup suggestion is "
    "allowed, you may send ONE short message offering to help connect that source "
    "right here in the chat (for example \"I can't see your calendar yet. Want me "
    "to help you connect Google Calendar? Takes about 2 minutes.\"). Never "
    "suggest anything not marked allowed. Do not repeat anything your notepad "
    "says was already sent. Otherwise reply with exactly [SILENT]."
)


# Prompts stored in jobs created by earlier versions. At run time a job still
# holding one of these uses the current prompt, so existing jobs get the new
# behaviour without being recreated; a prompt the user edited is left alone.
LEGACY_PROMPTS = frozenset({
    "Write a short morning brief for the user. Use whichever of these this profile actually has connected, and silently skip any that are missing: today's calendar events, mail that needs a reply, tasks due today, and open workstreams. If a google-workspace skill is installed, load it and follow its references/daily-brief.md. Highlight conflicts and anything time-critical. Keep it to a few lines. Do not send or change anything for the user; offer drafts instead. If nothing is connected, give a one-line good-morning with the date.",
    'Quietly check, using only the mail, calendar, tasks and open workstreams this profile has connected (skip missing ones silently), what changed since the last run. Message ONLY for something the user must act on today or that is time-critical. If you do, give the reason in one line and offer a draft; never send or act for them. Do not repeat anything your notepad says was already sent. Otherwise reply with exactly [SILENT].',
})


def current_prompt(job: Mapping[str, Any]) -> str:
    stored = str(job.get("prompt") or "")
    if stored in LEGACY_PROMPTS:
        return BRIEF_PROMPT if job.get(TAG) == gate.ROLE_BRIEF else CHECK_PROMPT
    return stored


def _gate_script_text() -> str:
    root = Path(__file__).resolve().parent.parent
    return (
        '"""Pre-check for proactive check-ins (written by /proactive checkin on)."""\n'
        "import sys\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "from cron.checkin_gate import main\n"
        "main()\n"
    )


def _write_gate_script() -> None:
    from clover_constants import get_clover_home
    scripts = Path(get_clover_home()) / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    path = scripts / GATE_SCRIPT
    text = _gate_script_text()
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def find_jobs() -> dict[str, dict]:
    from cron.jobs import list_jobs
    found: dict[str, dict] = {}
    for job in list_jobs(include_disabled=True):
        role = job.get(TAG)
        if role in (gate.ROLE_BRIEF, gate.ROLE_CHECK):
            found[role] = job
    return found


def check_hours(start: str, end: str, every: int) -> list[int]:
    """Hours of day (0-23) the check job fires: start, start+N, ... inside the window."""
    from datetime import datetime
    s = gate.parse_hhmm(start)
    hours: list[int] = []
    t = s
    for _ in range(24):
        h = (t // 60) % 24
        probe = datetime(2000, 1, 1, h, t % 60)
        if gate.in_active_hours(probe, start, end) and h not in hours:
            hours.append(h)
        t += every * 60
        if t - s >= 24 * 60:
            break
    return hours or [s // 60]


def schedules(cfg: Mapping[str, Any]) -> dict[str, Optional[str]]:
    """Cron expressions per role (``None`` brief when disabled with "")."""
    brief = str(cfg["morning_brief"] or "").strip()
    out: dict[str, Optional[str]] = {gate.ROLE_BRIEF: None}
    if brief:
        m = gate.parse_hhmm(brief)
        out[gate.ROLE_BRIEF] = f"{m % 60} {m // 60} * * *"
    start, end = cfg["active_hours"]["start"], cfg["active_hours"]["end"]
    minute = gate.parse_hhmm(start) % 60
    hours = ",".join(str(h) for h in check_hours(start, end, int(cfg["checks_every_hours"])))
    out[gate.ROLE_CHECK] = f"{minute} {hours} * * *"
    return out


def _deliver(cfg: Mapping[str, Any]) -> str:
    value = str(cfg.get("deliver") or "home").strip()
    if value != "home":
        return value
    # One chat app only: "all" would send the same message to every connected one.
    try:
        from cron.scheduler import _expand_routing_tokens
        homes = _expand_routing_tokens("all")
    except Exception:
        homes = []
    return homes[0] if homes else "all"


def _upsert(role: str, existing: Optional[dict], schedule: Optional[str], spec: dict) -> Optional[dict]:
    from cron.jobs import create_job, remove_job, update_job
    if schedule is None:
        if existing:
            remove_job(existing["id"])
        return None
    if existing:
        updates = {k: v for k, v in spec.items() if k not in ("schedule",)}
        updates["schedule"] = schedule
        if existing.get("enabled") is False:
            updates["enabled"] = True
        return update_job(existing["id"], updates)
    job = create_job(schedule=schedule, **spec)
    return update_job(job["id"], {TAG: role})


def enable(raw_cfg: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Create or update both jobs from settings. Idempotent."""
    cfg = gate.merged_settings(raw_cfg)
    scheds = schedules(cfg)
    _write_gate_script()
    jobs = find_jobs()
    deliver = _deliver(cfg)
    brief = _upsert(
        gate.ROLE_BRIEF, jobs.get(gate.ROLE_BRIEF), scheds[gate.ROLE_BRIEF],
        {"prompt": BRIEF_PROMPT, "name": BRIEF_NAME, "deliver": deliver},
    )
    check = _upsert(
        gate.ROLE_CHECK, jobs.get(gate.ROLE_CHECK), scheds[gate.ROLE_CHECK],
        {"prompt": CHECK_PROMPT, "name": CHECK_NAME, "deliver": deliver,
         "script": GATE_SCRIPT},
    )
    return {"brief": brief, "check": check}


def disable() -> int:
    """Delete both jobs. Returns how many were removed."""
    from cron.jobs import remove_job
    removed = 0
    for job in find_jobs().values():
        if remove_job(job["id"]):
            removed += 1
    return removed


def existing_overlap() -> list[str]:
    """Names of ordinary morning-brief / important-mail jobs that may duplicate this."""
    from cron.jobs import list_jobs
    names = []
    for job in list_jobs(include_disabled=True):
        if job.get(TAG):
            continue
        text = f"{job.get('name', '')}".lower()
        if "morning brief" in text or "morning briefing" in text or "important-mail" in text \
                or "important mail" in text:
            names.append(job.get("name") or job["id"])
    return names


SEEDED_MARKER = ".proactive_checkin_seeded"


def seed_default_jobs() -> bool:
    """Create the jobs once for a profile where check-ins are on by default.

    Gateway startup only. Skips (creating nothing) when the user turned
    check-ins off, when no chat app has a home channel yet (retried at the next
    start, no marker), when a marker shows seeding already ran (so jobs the
    user deleted via /cron stay deleted), or when check-in jobs, or a similar
    morning-brief job the user made themselves, already exist.
    Returns True when jobs were created.
    """
    try:
        from clover_cli import config as config_api
        from clover_constants import get_clover_home
        marker = Path(get_clover_home()) / SEEDED_MARKER
        if marker.exists():
            return False
        raw = (config_api.load_config(migrate=False).get("proactive") or {}).get("checkin") or {}
        cfg = gate.merged_settings(raw)
        if not cfg["enabled"]:
            return False
        if str(cfg.get("deliver") or "home").strip() == "home":
            from cron.scheduler import _expand_routing_tokens
            if not _expand_routing_tokens("all"):
                return False
        created = False
        if not find_jobs() and not existing_overlap():
            enable(raw)
            created = True
        marker.write_text("1", encoding="utf-8")
        return created
    except Exception:
        logger.debug("check-in seeding skipped", exc_info=True)
        return False
