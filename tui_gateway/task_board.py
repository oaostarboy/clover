"""Data for the phone Tasks tab, folded into ``delegation.status``.

``list_active_subagents()`` only carries {subagent_id, goal, model, started_at,
tool_count, status}. The helper's real checklist lives in the dispatch's
inflight record (``tools.delegation_inflight``), which ``agent.ack_checklist``
rewrites on every ``todo`` call. This module joins the two so each live child
gets ``title`` and ``checklist`` ([{id, content, status}]) plus ``done_steps`` /
``total_steps``, and adds the owner-blocking items and a short finished list.

Everything is additive: older clients ignore the new keys.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

_SID_INDEX = re.compile(r"^sa-(\d+)-")
_RECENT: Deque[Dict[str, Any]] = deque(maxlen=40)
_RECENT_LOCK = threading.Lock()
_STEP_STATES = ("pending", "in_progress", "completed", "cancelled")


def task_index_of(subagent_id: Any) -> Optional[int]:
    m = _SID_INDEX.match(str(subagent_id or ""))
    return int(m.group(1)) if m else None


def progress(checklist: List[Dict[str, Any]]) -> Dict[str, int]:
    total = len(checklist)
    done = sum(1 for s in checklist if s.get("status") == "completed")
    return {"done_steps": done, "total_steps": total}


def _clean_steps(raw: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for i, row in enumerate(raw[:40]):
        if not isinstance(row, dict):
            continue
        content = " ".join(str(row.get("content") or "").split())[:160]
        status = str(row.get("status") or "pending")
        if not content:
            continue
        out.append({"id": str(row.get("id") or f"s{i + 1}"), "content": content,
                    "status": status if status in _STEP_STATES else "pending"})
    return out


def enrich_active(active: List[Dict[str, Any]], read_record=None) -> List[Dict[str, Any]]:
    """Return copies of ``active`` with ``title``, ``checklist``, ``done_steps``, ``total_steps``."""
    if read_record is None:
        from tools.delegation_inflight import read_record as read_record  # type: ignore[no-redef]
    cache: Dict[str, Optional[Dict[str, Any]]] = {}
    out: List[Dict[str, Any]] = []
    for rec in active:
        item = dict(rec)
        did = str(item.get("delegation_id") or "")
        idx = task_index_of(item.get("subagent_id"))
        checklist: List[Dict[str, str]] = []
        title = ""
        if did and idx is not None:
            if did not in cache:
                cache[did] = read_record(did)
            record = cache[did] or {}
            tasks = record.get("tasks")
            if isinstance(tasks, list) and 0 <= idx < len(tasks) and isinstance(tasks[idx], dict):
                task = tasks[idx]
                checklist = _clean_steps(task.get("checklist"))
                title = " ".join(str(task.get("title") or "").split())[:120]
                if not title:
                    handoff = record.get("handoff") or {}
                    title = " ".join(str(handoff.get("work") or "").split())[:120] if isinstance(handoff, dict) else ""
        item["title"] = title or " ".join(str(item.get("goal") or "").split())[:80]
        item["checklist"] = checklist
        item.update(progress(checklist))
        out.append(item)
    return out


def note_finished(record: Dict[str, Any], outcome: str = "done") -> None:
    """Remember a finished helper for the Done column (bounded, in-memory)."""
    outcome = outcome if outcome in ("done", "failed", "stopped") else "done"
    entry = {
        "subagent_id": record.get("subagent_id"),
        "delegation_id": record.get("delegation_id"),
        "title": " ".join(str(record.get("goal") or "").split())[:100],
        "model": record.get("model"),
        "started_at": record.get("started_at"),
        "finished_at": time.time(),
        "outcome": outcome,
        "result": str(record.get("result_line") or "")[:160],
    }
    with _RECENT_LOCK:
        _RECENT.append(entry)


def recent_done(limit: int = 10) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 10), 40))
    with _RECENT_LOCK:
        items = list(_RECENT)
    return list(reversed(items))[:limit]


def needs_you_snapshot(sessions: Dict[str, Dict[str, Any]], lock: Any, clarify_for=None) -> List[Dict[str, Any]]:
    """Approvals and clarify questions currently blocking a live session."""
    items: List[Dict[str, Any]] = []
    with lock:
        snapshot = list(sessions.items())
    for sid, session in snapshot:
        if session.get("_finalized"):
            continue
        key = str(session.get("session_key") or "")
        title = str(session.get("title") or "")
        try:
            from tools.approval import get_pending_gateway_approval

            approval = get_pending_gateway_approval(key) if key else None
        except Exception:
            approval = None
        if approval:
            items.append({
                "type": "approval", "session_id": sid, "title": title,
                "request_id": approval.get("request_id"),
                "description": str(approval.get("description") or "")[:200],
                "command": str(approval.get("command") or "")[:200],
                "choices": approval.get("choices") or ["once", "deny"],
            })
        try:
            if clarify_for is None:
                from tui_gateway import server as _server

                clarify_for = _server._pending_clarify_request_payload
            clarify = clarify_for(sid)
        except Exception:
            clarify = None
        if clarify:
            items.append({
                "type": "clarify", "session_id": sid, "title": title,
                "request_id": clarify.get("request_id"),
                "question": str(clarify.get("question") or "")[:300],
                "choices": clarify.get("choices") or [],
            })
    return items
