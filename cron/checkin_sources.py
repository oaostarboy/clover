"""What a profile has connected, for proactive check-ins.

Computed here, in plain code, and handed to the check-in prompt as facts so the
model never guesses whether mail or calendar is connected. Also keeps the
small state that rate-limits "want to connect X?" suggestions: each missing
source is suggested at most once per 7 days, and never again after the user
declined twice (or after 3 suggestions nobody acted on).

Tasks are not a setup suggestion: Clover has built-in task tracking and no
chat-friendly setup flow exists for external task apps.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

SOURCES = ("mail", "calendar")
SUGGEST_EVERY = timedelta(days=7)
MAX_DECLINES = 2
MAX_IGNORED = 3

# Chat-friendly setup path per source (each is a skill the main agent can
# drive step by step inside the chat; nothing needs a terminal on the user's side).
SETUP_HINT = {
    "mail": "the google-workspace skill (Gmail, about 2 minutes) or the himalaya skill (any IMAP mailbox)",
    "calendar": "the google-workspace skill (Google Calendar, about 2 minutes)",
}
_MAIL_WORDS = ("gmail", "mail", "outlook", "imap")
_CAL_WORDS = ("calendar", "gcal", "caldav")


def _home() -> Path:
    from clover_constants import get_clover_home
    return Path(get_clover_home())


def _google_scopes() -> set[str]:
    path = _home() / "google_token.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict) or not (data.get("refresh_token") or data.get("token")):
        return set()
    raw = data.get("scopes") or data.get("scope") or []
    if isinstance(raw, str):
        raw = raw.split()
    return {str(s) for s in raw}


def _mcp_names() -> list[str]:
    try:
        from clover_cli.config import load_config_readonly
        servers = load_config_readonly().get("mcp_servers") or {}
        return [
            str(n).lower() for n, c in servers.items()
            if isinstance(c, dict) and c.get("enabled", True) is not False
        ]
    except Exception:
        return []


def _himalaya_configured() -> bool:
    for base in (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"),):
        cfg = base / "himalaya" / "config.toml"
        try:
            if "[accounts." in cfg.read_text(encoding="utf-8"):
                return True
        except OSError:
            pass
    return False


def connected() -> dict[str, bool]:
    """``{"mail": bool, "calendar": bool}`` from what the profile really has."""
    scopes = _google_scopes()
    names = _mcp_names()
    mail = (
        any("gmail" in s for s in scopes)
        or _himalaya_configured()
        or any(w in n for n in names for w in _MAIL_WORDS)
    )
    cal = (
        any("calendar" in s for s in scopes)
        or any(w in n for n in names for w in _CAL_WORDS)
    )
    return {"mail": mail, "calendar": cal}


# -- suggestion state --------------------------------------------------------

def _state_path() -> Path:
    return _home() / "cron" / "checkin_setup_state.json"


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".checkin_setup.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def _now() -> datetime:
    import clover_time
    return clover_time.now()


def next_suggestion(conn: dict[str, bool], state: dict[str, Any], now: datetime) -> Optional[str]:
    """The one missing source we may suggest right now, or ``None``."""
    for src in SOURCES:
        if conn.get(src):
            continue
        s = state.get(src) or {}
        if int(s.get("declines", 0)) >= MAX_DECLINES or int(s.get("suggested", 0)) >= MAX_IGNORED:
            continue
        last = s.get("last_suggested")
        if last:
            try:
                prev = datetime.fromisoformat(last)
                if (now if prev.tzinfo else now.replace(tzinfo=None)) - prev < SUGGEST_EVERY:
                    continue
            except ValueError:
                pass
        return src
    return None


def facts_block() -> str:
    """Plain-text facts appended to a check-in prompt. Remembers the pending suggestion."""
    conn = connected()
    state = load_state()
    sug = next_suggestion(conn, state, _now())
    state["pending"] = sug
    try:
        _save_state(state)
    except OSError:
        sug = None
    lines = ["## Connected sources (facts computed by Clover; do not contradict them)"]
    for src in SOURCES:
        lines.append(f"- {src}: {'connected' if conn[src] else 'NOT connected'}")
    lines.append("- tasks: built in (always available); no outside task app is needed")
    if sug:
        lines.append(
            f"- setup suggestion allowed this run: {sug} (help to connect it: {SETUP_HINT[sug]})"
        )
    else:
        lines.append("- setup suggestion allowed this run: none (do not suggest connecting anything)")
    return "\n".join(lines)


def commit_pending() -> None:
    """Called after a check-in message was really delivered: count the suggestion."""
    try:
        state = load_state()
        src = state.pop("pending", None)
        if src in SOURCES:
            s = state.setdefault(src, {})
            s["last_suggested"] = _now().isoformat()
            s["suggested"] = int(s.get("suggested", 0)) + 1
        _save_state(state)
    except Exception:
        pass


def record_decline(source: str) -> str:
    if source not in SOURCES:
        return f"unknown source {source!r}; use one of: {', '.join(SOURCES)}"
    state = load_state()
    s = state.setdefault(source, {})
    s["declines"] = int(s.get("declines", 0)) + 1
    if "stop" in sys.argv:
        s["declines"] = MAX_DECLINES
    _save_state(state)
    return f"recorded: {source} declines={s['declines']}"


if __name__ == "__main__":  # python -m cron.checkin_sources decline mail [stop]
    if len(sys.argv) >= 3 and sys.argv[1] == "decline":
        print(record_decline(sys.argv[2]))
    else:
        print(facts_block())
