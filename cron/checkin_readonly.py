"""Read-only capability set for proactive check-in cron runs.

Check-in jobs (``checkin_role`` 'brief' / 'check') may read mail, calendar,
tasks and files, but must never send, change or delete anything. The prompt
says so, but prompts are not enforcement; this module is.

Enforcement layers (all in code):

1. ``filter_agent_tools`` removes every non-read tool from the agent's tool
   list, so the model is never offered send/write tools (default-deny
   allowlist; unknown MCP tools pass only if their name reads as a read verb).
2. ``blocked_message`` is checked from ``_dispatch_pre_tool_call_hooks`` -
   the single choke point every dispatch path goes through - so a tool the
   model calls anyway (hallucinated, or via tool_call/tool_search) is refused
   and nothing executes. The run continues with the refusal as the result.
3. ``terminal`` stays available for read-only CLIs (himalaya, gws, ls, cat).
   Commands matching a known-mutating pattern are refused here, and the cron
   approval mode is forced to ``deny`` for check-ins (see
   ``tools.approval._get_cron_approval_mode``) regardless of config.

Best-effort: terminal is one tool for read and write, so a novel mutating
command that is neither on the deny list nor a dangerous-command pattern can
still run. Everything else is hard-blocked.
"""

from __future__ import annotations

import contextvars
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

REFUSAL = "check-ins are read-only; offer it to the user instead."

_CHECKIN_ROLE: contextvars.ContextVar = contextvars.ContextVar(
    "CLOVER_CHECKIN_READONLY", default=""
)


def enter(role: str):
    """Mark the current context as a read-only check-in run. Returns a token."""
    return _CHECKIN_ROLE.set(role or "")


def exit(token) -> None:  # noqa: A001
    if token is not None:
        _CHECKIN_ROLE.reset(token)


def is_checkin_run() -> bool:
    return bool(_CHECKIN_ROLE.get())


# Core tools a check-in may use. Everything not listed (and not read-shaped
# MCP) is blocked: send_message, write_file, patch, delegate_task, cronjob,
# memory, skill_manage, browser_*, computer_use, execute_code, process,
# image/video/tts generation, todo, kanban_*, clarify, ha_call_service ...
_ALLOWED_TOOLS = frozenset({
    "read_file", "search_files", "web_search", "web_extract", "session_search",
    "skill_view", "skills_list", "terminal",
    "tool_search", "tool_describe", "tool_call",  # tool_call is unwrapped and re-checked
    "ha_get_state", "ha_list_entities", "ha_list_services",
    "x_search", "vision_analyze",
})

_READ_VERBS = frozenset({
    "get", "list", "search", "read", "fetch", "query", "find", "show",
    "describe", "view", "check", "status", "count", "lookup", "stat", "stats",
    "inbox", "unread", "agenda", "export_read", "resolve",
})
_WRITE_VERBS = frozenset({
    "send", "reply", "forward", "move", "delete", "remove", "flag", "unflag",
    "create", "update", "insert", "patch", "write", "post", "put", "set",
    "add", "complete", "close", "archive", "trash", "mark", "draft", "edit",
    "modify", "cancel", "accept", "decline", "rsvp", "upload", "share",
    "click", "type", "press", "execute", "run", "call", "purchase", "buy",
    "order", "pay", "publish", "schedule", "approve", "reject", "commit",
    "push", "merge", "kill", "stop", "start", "restart", "install", "save",
    "store", "append", "rename", "copy", "empty", "clear", "reset",
})


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]


def is_read_tool(name: str) -> bool:
    """True when ``name`` may be offered/executed in a check-in run."""
    if name in _ALLOWED_TOOLS:
        return True
    toks = _tokens(name)
    if any(t in _WRITE_VERBS for t in toks):
        return False
    return any(t in _READ_VERBS for t in toks)


# --- terminal -------------------------------------------------------------

_SHELL_DENY = [
    # mail
    r"\bhimalaya\b.*\b(message|template|envelope|flag|folder|attachment)\b.*"
    r"\b(send|reply|forward|move|copy|delete|save|write|add|remove|expunge|purge|set)\b",
    r"\bhimalaya\b.*\b(send|reply|forward|delete|move|copy|flag|expunge)\b",
    r"\b(sendmail|mailx?|msmtp|mutt|swaks)\b",
    # google workspace
    r"\bgws\b.*\b(send|reply|forward|insert|patch|update|delete|trash|untrash|"
    r"create|modify|batchdelete|import|move|clear|watch|stop|quickadd)\b",
    r"\bgcalcli\b.*\b(add|delete|edit|import|quick|remind)\b",
    # tasks / notes / vcs / packages
    r"\b(todoist|task|tasks|things|remind|reminders)\b.*\b(add|done|complete|delete|edit|close|modify)\b",
    r"\bgit\s+(push|commit|reset|checkout|clean|rm|mv|rebase|merge|stash|tag|apply|am|cherry-pick)\b",
    r"\bgh\s+\S+\s+(create|edit|delete|close|merge|comment|review|reopen)\b",
    r"\b(pip3?|uv|npm|yarn|pnpm|apt|apt-get|brew|cargo)\s+(install|uninstall|remove|add|publish)\b",
    r"\bclover\s+(cron|config|gateway|update)\b",
    # filesystem mutation
    r"(^|[;&|(`]\s*|\bsudo\s+|\bxargs\s+)(rm|rmdir|mv|cp|dd|truncate|shred|chmod|chown|ln|mkdir|touch|tee|install)\b",
    r"\bsed\b[^|;]*\s-[a-zA-Z]*i",
    r"\bfind\b.*\s-(delete|exec|execdir)\b",
    r"(^|[^<>0-9&])>{1,2}\s*(?!/dev/null|&)\S",       # output redirection to a file
    # network writes
    r"\bcurl\b.*(\s-X\s*(POST|PUT|PATCH|DELETE)|\s--request\s+(POST|PUT|PATCH|DELETE)|\s(-d|--data\S*|-F|--form|-T|--upload-file)\b)",
    r"\bwget\b.*--(post|method)",
    r"\b(kill|killall|pkill|systemctl\s+(start|stop|restart|enable|disable)|shutdown|reboot)\b",
]
_SHELL_DENY_RE = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _SHELL_DENY]


def terminal_command_blocked(command: str) -> bool:
    cmd = command or ""
    return any(rx.search(cmd) for rx in _SHELL_DENY_RE)


# --- public API -----------------------------------------------------------

def blocked_message(tool_name: str, args: Optional[dict]) -> Optional[str]:
    """Refusal text if this call is not allowed in a check-in run, else None."""
    if not is_checkin_run():
        return None
    reason = None
    if tool_name == "terminal":
        cmd = (args or {}).get("command") if isinstance(args, dict) else ""
        if terminal_command_blocked(str(cmd or "")):
            reason = "mutating terminal command"
    elif not is_read_tool(tool_name):
        reason = "non-read tool"
    if reason is None:
        return None
    logger.info("check-in read-only: refused %s (%s)", tool_name, reason)
    return REFUSAL


def filter_agent_tools(agent: Any) -> list[str]:
    """Drop non-read tools from a constructed agent. Returns removed names."""
    removed = []
    keep = []
    for t in list(getattr(agent, "tools", None) or []):
        n = (t.get("function") or {}).get("name", "")
        if is_read_tool(n):
            keep.append(t)
        else:
            removed.append(n)
    agent.tools = keep
    agent.valid_tool_names = {t["function"]["name"] for t in keep}
    return removed
