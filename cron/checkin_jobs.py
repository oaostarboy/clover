"""Create, update and remove the two ordinary cron jobs behind proactive check-ins.

Only an explicit ``/proactive checkin on`` calls ``enable``. Nothing in install,
update, config migration or gateway startup imports this to create jobs.
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
    "Keep it to a few lines. Do not send or change anything for the user; "
    "offer drafts instead. If nothing is connected, give a one-line "
    "good-morning with the date."
)

CHECK_PROMPT = (
    "Quietly check, using only the mail, calendar, tasks and open workstreams "
    "this profile has connected (skip missing ones silently), what changed "
    "since the last run. Message ONLY for something the user must act on today "
    "or that is time-critical. If you do, give the reason in one line and offer "
    "a draft; never send or act for them. Do not repeat anything your notepad "
    "says was already sent. Otherwise reply with exactly [SILENT]."
)


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
    return "all" if value == "home" else value


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
