"""Deterministic gate for proactive check-ins (no model call).

The check job runs this as its pre-check script. When it prints
``{"wakeAgent": false}`` the scheduler skips the agent run entirely, so an
empty or disallowed check costs nothing. The decision is a pure function of
settings, the clock and the recorded send times, so it is easy to test.

Sends are recorded by the scheduler only when a check-in message was really
delivered (``record_send``). A ``[SILENT]`` run delivers nothing and records
nothing.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

SOURCE_TAG = "proactive_checkin"
ROLE_BRIEF = "brief"
ROLE_CHECK = "check"

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "timezone": "",
    "morning_brief": "08:00",
    "checks_every_hours": 3,
    "active_hours": {"start": "09:00", "end": "20:00"},
    "max_per_day": 3,
    "min_gap_minutes": 90,
    "sources": ["mail", "calendar", "tasks", "workstreams"],
    "deliver": "home",
    "paused_until": "",
}

_KEEP_DAYS = 3


def parse_hhmm(value: Any, *, allow_24: bool = False) -> int:
    """``"09:30"`` -> minutes after midnight. Raises ValueError when invalid."""
    text = str(value).strip()
    try:
        hh, mm = text.split(":")
        h, m = int(hh), int(mm)
    except ValueError:
        raise ValueError(f"{value!r} is not a time like 08:00") from None
    if allow_24 and h == 24 and m == 0:
        return 24 * 60
    if not (0 <= h <= 23 and 0 <= m <= 59) or len(hh) > 2 or len(mm) != 2:
        raise ValueError(f"{value!r} is not a time like 08:00")
    return h * 60 + m


def merged_settings(raw: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Defaults overlaid with whatever the user saved (one nested level)."""
    out = json.loads(json.dumps(DEFAULTS))
    for key, value in (raw or {}).items():
        if key == "active_hours" and isinstance(value, Mapping):
            out["active_hours"].update({k: v for k, v in value.items() if v not in (None, "")})
        elif key in out:
            out[key] = value
    return out


def in_active_hours(local_now: datetime, start: str, end: str) -> bool:
    """Wall-clock window check. End is exclusive; ``start > end`` wraps midnight.

    Uses the local wall clock in the user's zone, so a DST jump moves no
    boundary. ``start == end`` means always active.
    """
    s, e = parse_hhmm(start), parse_hhmm(end, allow_24=True)
    now_min = local_now.hour * 60 + local_now.minute
    if s == e:
        return True
    if s < e:
        return s <= now_min < e
    return now_min >= s or now_min < e


def resolve_tz(name: str = "") -> Optional[tzinfo]:
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            logger.warning("checkin: unknown timezone %r; using Clover's timezone", name)
    import clover_time
    return clover_time.get_timezone()


def _sends_path() -> Path:
    from clover_constants import get_clover_home
    return Path(get_clover_home()) / "cron" / "checkin_sends.json"


def load_sends() -> list[datetime]:
    try:
        data = json.loads(_sends_path().read_text(encoding="utf-8"))
        return [datetime.fromisoformat(s) for s in data if isinstance(s, str)]
    except (FileNotFoundError, ValueError, OSError):
        return []


def record_send(when: Optional[datetime] = None) -> None:
    """Record one real check-in delivery (called by the scheduler)."""
    import clover_time
    when = when or clover_time.now()
    cutoff = when - timedelta(days=_KEEP_DAYS)
    keep = [d for d in load_sends() if _aware(d) > _aware(cutoff)] + [when]
    path = _sends_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".checkin_sends.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump([d.isoformat() for d in keep], fh)
    os.replace(tmp, path)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.astimezone()


def sends_today(sends: list[datetime], local_now: datetime) -> int:
    tz = local_now.tzinfo
    today = local_now.date()
    return sum(1 for d in sends if _aware(d).astimezone(tz).date() == today)


def evaluate(settings: Mapping[str, Any], now: datetime, sends: list[datetime]) -> tuple[bool, str]:
    """Return ``(wake, reason)``. ``now`` must be timezone-aware."""
    cfg = merged_settings(settings)
    if not cfg["enabled"]:
        return False, "disabled"
    tz = resolve_tz(str(cfg["timezone"] or ""))
    local_now = now.astimezone(tz) if tz else _aware(now)
    paused = str(cfg.get("paused_until") or "").strip()
    if paused:
        try:
            until = datetime.fromisoformat(paused)
            if _aware(until) > _aware(now):
                return False, f"paused until {paused}"
        except ValueError:
            pass
    hours = cfg["active_hours"]
    try:
        if not in_active_hours(local_now, hours["start"], hours["end"]):
            return False, f"outside active hours {hours['start']}-{hours['end']}"
    except ValueError:
        return False, "invalid active hours"
    try:
        cap = int(cfg["max_per_day"])
    except (TypeError, ValueError):
        cap = DEFAULTS["max_per_day"]
    used = sends_today(sends, local_now)
    if used >= cap:
        return False, f"daily cap reached ({used}/{cap})"
    try:
        gap = int(cfg["min_gap_minutes"])
    except (TypeError, ValueError):
        gap = DEFAULTS["min_gap_minutes"]
    if sends:
        last = max(_aware(d) for d in sends)
        if _aware(now) - last < timedelta(minutes=gap):
            return False, f"last check-in under {gap} min ago"
    return True, f"ok ({used}/{cap} sent today)"


def main() -> None:
    """Script entry: print one JSON line for the scheduler's wake gate.

    Fails closed: any error means do not wake the agent.
    """
    try:
        from clover_cli.config import load_config
        import clover_time
        cfg = (load_config(migrate=False).get("proactive") or {}).get("checkin") or {}
        wake, reason = evaluate(cfg, clover_time.now(), load_sends())
    except Exception as exc:  # noqa: BLE001
        wake, reason = False, f"gate error: {exc}"
    logger.info("checkin gate: wake=%s (%s)", wake, reason)
    print(json.dumps({"wakeAgent": wake, "reason": reason}))
