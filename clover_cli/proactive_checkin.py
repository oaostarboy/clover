"""``/proactive checkin ...`` — morning brief and capped check-ins (on by default).

Parsing and persistence reuse clover_cli.proactive (strict raw read, managed
policy pins, readback). Only ``on`` and later setting changes touch cron jobs,
and only through cron.jobs. Nothing here runs at install or update; the gateway
seeds the jobs once at startup (cron.checkin_jobs.seed_default_jobs).
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta

from clover_cli import config as config_api
from clover_cli import managed_scope

HELP = (
    "/proactive checkin on|off|status — morning brief and quiet check-ins\n"
    "/proactive checkin brief 07:30|off; hours 9-20; every 30m|1h|3h; max 2; "
    "pause 24h|resume"
)
LOCKED = "Controls locked by command policy: admin access required."
FAILED = ("Check-in settings could not be confirmed; nothing was changed. "
          "Check config permissions and administrator policy.")
USAGE = "Not understood; nothing changed.\n" + HELP

_MUTATING = {"on", "off", "brief", "hours", "every", "max", "pause", "resume"}


def is_mutation(parts: list[str]) -> bool:
    """``parts`` are the words after ``checkin``."""
    return bool(parts) and parts[0] in _MUTATING


def _parse_clock(text: str, *, allow_24: bool = False) -> str:
    from cron.checkin_gate import parse_hhmm
    t = text.strip()
    if re.fullmatch(r"\d{1,2}", t):
        t += ":00"
    minutes = parse_hhmm(t if re.fullmatch(r"\d{1,2}:\d{2}", t) else "x", allow_24=allow_24)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _parse_hours(text: str) -> tuple[str, str]:
    m = re.fullmatch(r"\s*(\d{1,2}(?::\d{2})?)\s*-\s*(\d{1,2}(?::\d{2})?)\s*", text)
    if not m:
        raise ValueError("Hours look like 9-20 or 09:00-20:00.")
    start, end = _parse_clock(m.group(1)), _parse_clock(m.group(2), allow_24=True)
    if start == end:
        raise ValueError("Start and end hours must differ.")
    return start, end


def _parse_pause(text: str) -> timedelta:
    m = re.fullmatch(r"(\d{1,3})\s*([hd])", text.strip().lower())
    if not m or int(m.group(1)) < 1:
        raise ValueError("Pause looks like 24h or 3d.")
    n = int(m.group(1))
    delta = timedelta(hours=n) if m.group(2) == "h" else timedelta(days=n)
    if delta > timedelta(days=30):
        raise ValueError("Pause can be at most 30 days.")
    return delta


def _parse_every(text: str) -> int:
    from cron.checkin_jobs import valid_interval
    from cron.checkin_gate import MIN_INTERVAL_MIN
    msg = "Interval looks like 30m, 1h or 3h (15 minutes to 24 hours; minutes: 15, 20 or 30)."
    m = re.fullmatch(r"(\d{1,4})\s*([mh]?)", text.strip().lower())
    if not m:
        raise ValueError(msg)
    n = int(m.group(1))
    minutes = n if m.group(2) == "m" else n * 60  # a bare number means hours, as before
    if minutes < MIN_INTERVAL_MIN or not valid_interval(minutes):
        raise ValueError(msg)
    return minutes


def gate_interval_label(cfg: dict) -> str:
    from cron import checkin_gate as gate
    return gate.interval_label(gate.interval_minutes(cfg))


def _int_in(text: str, lo: int, hi: int, label: str) -> int:
    if not re.fullmatch(r"\d{1,3}", text.strip()):
        raise ValueError(f"{label} must be a whole number from {lo} to {hi}.")
    n = int(text)
    if not lo <= n <= hi:
        raise ValueError(f"{label} must be a whole number from {lo} to {hi}.")
    return n


def _apply(parts: list[str], cfg: dict) -> str:
    """Mutate ``cfg`` (the checkin mapping) for one command; return a short note."""
    action, rest = parts[0], " ".join(parts[1:])
    if action in {"on", "off", "resume"} and rest:
        raise ValueError(USAGE)
    if action == "on":
        cfg["enabled"] = True
    elif action == "off":
        cfg["enabled"] = False
    elif action == "resume":
        cfg["paused_until"] = ""
    elif action == "brief":
        if rest == "off":
            cfg["morning_brief"] = ""
        else:
            cfg["morning_brief"] = _parse_clock(rest)
    elif action == "hours":
        start, end = _parse_hours(rest)
        cfg["active_hours"] = {"start": start, "end": end}
    elif action == "every":
        cfg["checks_every_minutes"] = _parse_every(rest)
    elif action == "max":
        cfg["max_per_day"] = _int_in(rest, 1, 20, "Daily limit")
    elif action == "pause":
        import clover_time
        cfg["paused_until"] = (clover_time.now() + _parse_pause(rest)).isoformat(timespec="minutes")
    else:
        raise ValueError(USAGE)
    return action


def _short(hhmm: str) -> str:
    h, m = hhmm.split(":")
    return f"{int(h)}:{m}"


def _summary(cfg: dict) -> str:
    h = cfg["active_hours"]
    brief = f"Morning brief {_short(cfg['morning_brief'])}" if cfg["morning_brief"] else "No morning brief"
    return (f"{brief}, checks every {gate_interval_label(cfg)} from "
            f"{_short(h['start'])} to {_short(h['end'])}, "
            f"at most {cfg['max_per_day']} a day.")


def _home_channel_ok() -> bool:
    try:
        from cron.scheduler import _expand_routing_tokens
        return bool(_expand_routing_tokens("all"))
    except Exception:
        return False


def _gateway_running() -> bool:
    try:
        from gateway.status import is_gateway_running
        return bool(is_gateway_running())
    except Exception:
        return False


def status_text() -> str:
    from cron import checkin_gate as gate
    from cron import checkin_jobs
    import clover_time
    cfg = gate.merged_settings(
        (config_api.load_config(migrate=False).get("proactive") or {}).get("checkin") or {})
    if not cfg["enabled"]:
        return "Check-ins are off. Turn on: /proactive checkin on\n" + _summary(cfg)
    lines = ["Check-ins are on. " + _summary(cfg)]
    now = clover_time.now()
    paused = str(cfg.get("paused_until") or "")
    if paused:
        try:
            if datetime.fromisoformat(paused) > now.astimezone():
                lines.append(f"Paused until {paused.replace('T', ' ')}. Resume: /proactive checkin resume")
        except ValueError:
            pass
    jobs = checkin_jobs.find_jobs()
    nxt = [j.get("next_run_at") for j in jobs.values() if j.get("next_run_at")]
    lines.append("Next run: " + (min(nxt).replace("T", " ")[:16] if nxt else "none scheduled"))
    if len(jobs) < (2 if cfg["morning_brief"] else 1):
        lines.append("Some check-in jobs are missing. Run /proactive checkin on to repair.")
    lines.append(f"Sent today: {gate.sends_today(gate.load_sends(), now)} of {cfg['max_per_day']}")
    if not _home_channel_ok():
        lines.append("Warning: no home channel is set, so nothing can be delivered. Use /sethome in your chat.")
        if not jobs:
            lines.append("No check-in jobs yet: they are created when the gateway starts with a home channel set.")
    if not _gateway_running():
        lines.append("Warning: the gateway is not running, so nothing will run. Start it with: clover gateway start")
    lines.append("Turn off: /proactive checkin off")
    return "\n".join(lines)


def checkin_command(parts: list[str], *, can_change: bool) -> str:
    """Handle ``/proactive checkin <parts>``. Never calls a model."""
    from clover_cli import proactive as P
    if not parts or parts == ["status"]:
        try:
            return status_text()
        except Exception:
            return FAILED
    if parts[0] not in _MUTATING:
        return USAGE
    if not can_change:
        return LOCKED
    try:
        with config_api._CONFIG_LOCK:
            managed_scope.load_managed_config(strict=True)
            raw = config_api.require_readable_config_before_write()
            updated = copy.deepcopy(raw)
            root = updated.setdefault("proactive", {})
            P._validate(root)
            from cron import checkin_gate as gate
            cur = root.setdefault("checkin", {})
            if not isinstance(cur, dict):
                raise ValueError("proactive.checkin must be a mapping")
            cfg = gate.merged_settings(cur)
            try:
                action = _apply(parts, cfg)
            except ValueError as exc:
                return str(exc)
            # Validate the schedule before anything is written.
            from cron import checkin_jobs
            checkin_jobs.schedules(cfg)
            root["checkin"] = cfg
            note = P._persist(raw, updated)
            if not note.startswith(("Saved", "Already saved")):
                return note
        warn = ""
        if cfg["enabled"]:
            if action == "on":
                overlap = checkin_jobs.existing_overlap()
                if overlap:
                    warn = ("\nNote: you already have a similar job (" + ", ".join(overlap[:2]) +
                            "). You may get duplicates; remove one with /cron.")
            checkin_jobs.enable(cfg)
        elif action == "off":
            checkin_jobs.disable()
    except managed_scope.ManagedPolicyError as exc:
        return str(exc)
    except Exception:
        return FAILED
    if action == "on":
        return f"Check-ins on. {_summary(cfg)} Turn off: /proactive checkin off" + warn
    if action == "off":
        return "Check-ins off. Both scheduled jobs removed."
    if action == "pause":
        return f"Check-ins paused until {cfg['paused_until'].replace('T', ' ')}. Resume: /proactive checkin resume"
    if action == "resume":
        return "Check-ins resumed."
    state = "" if cfg["enabled"] else " Saved; takes effect when you turn check-ins on."
    return "Saved. " + _summary(cfg) + state
