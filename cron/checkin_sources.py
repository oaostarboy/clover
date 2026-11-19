"""What a profile has connected, for proactive check-ins.

Computed here, in plain code, and handed to the check-in prompt as facts so the
model never guesses whether mail or calendar is connected. Also keeps the
small state that rate-limits "want to connect X?" suggestions: each missing
source is suggested at most once per 7 days, and never again after the user
declined twice (or after 3 suggestions nobody acted on).

The catalog below is small and honest: each item has a deterministic
"connected?" check, the real setup path, and a relevance rule over the user's
recent messages, so suggestions fit what they actually do.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

SUGGEST_EVERY = timedelta(days=7)
MAX_DECLINES = 2
MAX_IGNORED = 3
RELEVANCE_DAYS = 7


@dataclass(frozen=True)
class Item:
    """One thing a profile can set up, with how to detect it and how to do it."""
    key: str
    label: str
    hint: str                      # the real setup path, in words
    chat_setup: bool = True        # False: the agent cannot do it from chat; say how instead
    relevance: str = ""            # "" = always relevant; else a key in RELEVANCE_RULES


CATALOG: tuple[Item, ...] = (
    Item("mail", "mail",
         "the google-workspace skill (Gmail, about 2 minutes) or the himalaya skill (any IMAP mailbox)"),
    Item("calendar", "calendar", "the google-workspace skill (Google Calendar, about 2 minutes)"),
    Item("tasks", "an outside task app",
         "the notion skill (needs a Notion integration token); Todoist and Linear connect through "
         "an MCP server (`clover mcp`)", relevance="tasks"),
    Item("voice_replies", "spoken replies",
         "send /voice tts in this chat to turn on spoken replies (the user types it; you cannot "
         "switch it on for them)", chat_setup=False, relevance="voice"),
    Item("iphone", "the iPhone app",
         "open Clover on the desktop and pair the phone with the QR code it shows (done on the "
         "desktop, not in chat)", chat_setup=False, relevance="telegram_heavy"),
)
# A second chat platform is deliberately not in the catalog: never unprompted.
SOURCES = tuple(i.key for i in CATALOG)
_BY_KEY = {i.key: i for i in CATALOG}
SETUP_HINT = {i.key: i.hint for i in CATALOG}
_MAIL_WORDS = ("gmail", "mail", "outlook", "imap")
_CAL_WORDS = ("calendar", "gcal", "caldav")
_TASK_WORDS = ("todoist", "linear", "notion", "things", "ticktick", "asana", "trello", "clickup")
_TASK_ENV = ("TODOIST_API_KEY", "TODOIST_API_TOKEN", "LINEAR_API_KEY", "NOTION_API_KEY",
             "NOTION_API_TOKEN", "TICKTICK_TOKEN", "ASANA_ACCESS_TOKEN")

# Relevance: cheap local counts over the user's own messages from the last
# week. No model call. A rule returns True when the user's own activity
# suggests the item would help them.
_TASK_RE = re.compile(r"\b(to-?dos?|tasks?|deadlines?|due (?:date|today|tomorrow|friday|monday)|"
                      r"remind(?:er)?s?)\b", re.I)
MIN_TASK_MENTIONS = 3
MIN_VOICE_NOTES = 3
MIN_TELEGRAM_MESSAGES = 15


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


def _dotenv_keys() -> set[str]:
    keys: set[str] = set()
    try:
        for line in (_home() / ".env").read_text(encoding="utf-8").splitlines():
            name, sep, val = line.partition("=")
            if sep and val.strip().strip("'\""):
                keys.add(name.strip().removeprefix("export ").strip())
    except OSError:
        pass
    return keys


def _tasks_connected(names: list[str]) -> bool:
    have = _dotenv_keys() | {k for k in _TASK_ENV if os.environ.get(k)}
    return any(k in have for k in _TASK_ENV) or any(w in n for n in names for w in _TASK_WORDS)


def _voice_on() -> bool:
    try:
        from clover_cli.config import load_config_readonly
        return bool((load_config_readonly().get("voice") or {}).get("auto_tts"))
    except Exception:
        return False


def _iphone_paired() -> bool:
    try:
        from clover_cli.mobile_pairing import get_device_store
        return bool(get_device_store().list())
    except Exception:
        return False


def connected() -> dict[str, bool]:
    """``{key: bool}`` for every catalog item, from what the profile really has."""
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
    return {"mail": mail, "calendar": cal, "tasks": _tasks_connected(names),
            "voice_replies": _voice_on(), "iphone": _iphone_paired()}


# -- relevance (from the user's own recent messages) -----------------------------

def recent_user_messages(days: int = RELEVANCE_DAYS) -> list[tuple[str, str]]:
    """``[(source, text)]`` of the user's own messages in the last ``days`` days.

    Read-only SQLite query on the session store. Cron/check-in sessions and
    bracketed system notes are excluded, so only things the user wrote count.
    """
    import sqlite3
    db = _home() / "state.db"
    if not db.exists():
        return []
    cutoff = _now().timestamp() - days * 86400
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            rows = conn.execute(
                "SELECT s.source, m.content FROM messages m JOIN sessions s ON s.id = m.session_id "
                "WHERE m.role = 'user' AND m.timestamp >= ? AND s.source != 'cron' "
                "AND m.content IS NOT NULL ORDER BY m.id DESC LIMIT 4000", (cutoff,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [(str(src or ""), str(txt)) for src, txt in rows if isinstance(txt, str)]


def _is_voice_note(text: str) -> bool:
    t = text.strip()
    # The gateway turns a voice note into its transcript as one quoted line.
    return (len(t) > 2 and t[0] == '"' and t[-1] == '"' and "\n" not in t) \
        or t.startswith("[The user sent a voice message")


def relevant_items(messages: Optional[list[tuple[str, str]]] = None) -> frozenset[str]:
    """Catalog keys the user's recent activity makes worth suggesting."""
    msgs = recent_user_messages() if messages is None else messages
    own = [(src, t) for src, t in msgs if not t.lstrip().startswith("[")
           or t.startswith("[The user sent a voice message")]
    out: set[str] = set()
    if sum(1 for _, t in own if _TASK_RE.search(t)) >= MIN_TASK_MENTIONS:
        out.add("tasks")
    if sum(1 for _, t in own if _is_voice_note(t)) >= MIN_VOICE_NOTES:
        out.add("voice")
    if sum(1 for src, _ in own if src == "telegram") >= MIN_TELEGRAM_MESSAGES:
        out.add("telegram_heavy")
    return frozenset(out)


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


def next_suggestion(conn: dict[str, bool], state: dict[str, Any], now: datetime,
                    relevant: frozenset[str] = frozenset()) -> Optional[str]:
    """The one missing item we may suggest right now, or ``None``.

    ``relevant`` holds the relevance signals matched from recent activity;
    items with a relevance rule are skipped unless theirs is present.
    """
    for item in CATALOG:
        src = item.key
        if conn.get(src):
            continue
        if item.relevance and item.relevance not in relevant:
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
    """Plain-text facts appended to a check-in prompt. Remembers the pending suggestion.

    Only items that matched the user's own activity are mentioned, plus the
    always-relevant mail and calendar.
    """
    conn = connected()
    state = load_state()
    relevant = relevant_items()
    sug = next_suggestion(conn, state, _now(), relevant)
    state["pending"] = sug
    try:
        _save_state(state)
    except OSError:
        sug = None
    lines = ["## Connected sources (facts computed by Clover; do not contradict them)"]
    for item in CATALOG:
        if item.relevance and item.relevance not in relevant and not conn[item.key]:
            continue  # unmatched: not mentioned at all
        lines.append(f"- {item.key}: {'connected' if conn[item.key] else 'NOT connected'}")
    if not (sug == "tasks"):
        lines.append("- tasks: built in (always available); no outside task app is needed")
    if sug:
        item = _BY_KEY[sug]
        if item.chat_setup:
            lines.append(f"- setup suggestion allowed this run: {sug} (help to connect it: {item.hint})")
            lines.append(
                f"- So {sug} is not connected and this is its setup turn: unless something "
                "more pressing needs the user today, your message is ONE short offer to "
                f"help connect {item.label} right here in the chat. Do not reply [SILENT]."
            )
        else:
            lines.append(f"- setup suggestion allowed this run: {sug} (NO chat setup exists for it)")
            lines.append(
                f"- This is the setup turn for {item.label}, but you cannot do it from the chat. "
                "Unless something more pressing needs the user today, your message is ONE short "
                f"tip telling them how: {item.hint}. Do not offer to do it for them. "
                "Do not reply [SILENT]."
            )
    else:
        lines.append("- setup suggestion allowed this run: none (do not suggest connecting anything)")
    return "\n".join(lines)


def commit_pending() -> None:
    """Called after a check-in message was really delivered: count the suggestion."""
    try:
        state = load_state()
        src = state.pop("pending", None)
        if src in _BY_KEY:
            s = state.setdefault(src, {})
            s["last_suggested"] = _now().isoformat()
            s["suggested"] = int(s.get("suggested", 0)) + 1
        _save_state(state)
    except Exception:
        pass


def record_decline(source: str) -> str:
    if source not in _BY_KEY:
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
