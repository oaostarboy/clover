"""Proactive triage + risk gate for the owner's to-do list.

Flow: items are added -> ``triage_pending`` batches untriaged items into one
short LLM call -> ``parse_triage`` validates the answer -> ``decide`` applies
the risk gate -> eligible items are started as normal agent sessions via an
injected ``spawn`` callable (kept injectable so tests never touch a model).

Risk rules (the gate is deterministic and does NOT trust the model alone):
  * A keyword screen (``looks_external``) flags anything that sends, posts,
    buys, deletes, contacts people, changes accounts or schedules invites.
    A flagged item is forced to ``external`` + ``risk=high``; it is at most
    DRAFTED (status ``needs_you``) and never auto-started.
  * Auto-start requires ALL of: owner == 'clover', risk == 'low', not
    external, settings.auto_start on, global kill switch off, item created
    after settings.enabled_at, and fewer than MAX_CONCURRENT running.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import todo_store as store

MAX_CONCURRENT = 2
KILL_SWITCH_ENV = "CLOVER_TODOS_AUTOSTART_DISABLED"

_EXTERNAL = re.compile(
    r"\b(send|email|e-mail|mail|text|message|dm|reply|respond|post|tweet|publish|share|announce|"
    r"buy|purchase|order|pay|book|reserve|subscribe|donate|transfer|invoice|"
    r"delete|remove|cancel|unsubscribe|wipe|erase|"
    r"call|phone|contact|ping|invite|schedule|calendar|rsvp|"
    r"apply|submit|sign up|signup|register|"
    r"password|account|login|log in|settings?|deploy|release|push|merge)\b",
    re.I,
)

TRIAGE_SYSTEM = (
    "You triage a person's personal to-do items for their AI assistant Clover. Reply with ONLY a JSON array, "
    "one object per item, in the same order: "
    '{"id": "<id>", "owner": "clover|you|together", "risk": "low|medium|high", "external": true|false, '
    '"plan": "<one short line>", "notes": "<optional one-line description or empty>"}. '
    "owner=clover only for work Clover can do alone on the computer (research, drafting, summarising, planning, "
    "coding in her own workspace, organising files). owner=you for physical or personal errands. owner=together "
    "when it needs the person's input. external=true if it sends, posts, buys, deletes, contacts anyone, changes "
    "an account or creates calendar invites. Be conservative."
)


def looks_external(text: str) -> bool:
    return bool(_EXTERNAL.search(text or ""))


def build_prompt(items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    payload = [{"id": i["id"], "text": i["text"], "notes": i.get("notes", "")} for i in items]
    return [{"role": "system", "content": TRIAGE_SYSTEM}, {"role": "user", "content": json.dumps(payload)}]


def parse_triage(raw: Any, items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Validate a model reply. Unknown ids dropped, bad fields defaulted conservatively, junk -> {}."""
    text = raw if isinstance(raw, str) else ""
    m = re.search(r"\[.*\]", text, re.S)
    try:
        arr = json.loads(m.group(0)) if m else json.loads(text)
    except ValueError:
        arr = None
    if isinstance(arr, dict):
        arr = arr.get("items") if isinstance(arr.get("items"), list) else [arr]
    if not isinstance(arr, list):
        return {}
    by_id = {i["id"]: i for i in items}
    out: Dict[str, Dict[str, Any]] = {}
    for row in arr:
        if not isinstance(row, dict) or row.get("id") not in by_id:
            continue
        item = by_id[row["id"]]
        owner = row.get("owner") if row.get("owner") in store.OWNERS else "you"
        risk = row.get("risk") if row.get("risk") in store.RISKS else "high"
        external = bool(row.get("external")) or looks_external(item["text"])
        if external:
            risk = "high"
        out[item["id"]] = {
            "owner": owner, "risk": risk, "external": external,
            "plan": str(row.get("plan") or "")[:200], "notes": str(row.get("notes") or "")[:300],
        }
    return out


def heuristic(item: Dict[str, Any]) -> Dict[str, Any]:
    """Offline fallback when no model is reachable: never auto-starts anything."""
    ext = looks_external(item["text"])
    return {"owner": "together" if ext else "you", "risk": "high" if ext else "medium", "external": ext,
            "plan": "", "notes": ""}


def decide(item: Dict[str, Any], tri: Dict[str, Any], settings: Dict[str, Any], running: int,
           *, killed: bool = False) -> str:
    """-> 'start' | 'draft' | 'hold'.  The only place an item may be cleared to run."""
    if tri.get("owner") != "clover":
        return "hold"
    external = bool(tri.get("external")) or looks_external(item.get("text", ""))
    if external or tri.get("risk") != "low":
        return "draft" if external or tri.get("risk") == "high" else "hold"
    if killed or not settings.get("auto_start", True):
        return "hold"
    if float(item.get("created_at") or 0) < float(settings.get("enabled_at") or 0):
        return "hold"
    if running >= MAX_CONCURRENT:
        return "hold"
    return "start"


def running_count(items: List[Dict[str, Any]]) -> int:
    return sum(1 for i in items if i.get("auto_started") and i.get("status") in ("queued", "working") and not i.get("done"))


def task_prompt(item: Dict[str, Any]) -> str:
    return (
        f"Owner to-do: {item['text']}\n"
        + (f"Notes: {item['notes']}\n" if item.get("notes") else "")
        + (f"Plan: {item['plan']}\n" if item.get("plan") else "")
        + "\nDo this yourself now, inside your own workspace only. Research, draft, summarise, plan or build. "
        "Do NOT send, post, buy, delete, contact anyone, change accounts or create calendar invites. If the task "
        "needs any of that, write the draft and end your final message with a line 'NEEDS_APPROVAL: <what you want to do>'. "
        "Finish with a 1-3 sentence result summary on its own line starting 'SUMMARY:'."
    )


def parse_outcome(final_text: str) -> Dict[str, str]:
    t = final_text or ""
    ap = re.search(r"^\s*NEEDS_APPROVAL:\s*(.+)$", t, re.M)
    sm = re.search(r"^\s*SUMMARY:\s*(.+)$", t, re.M)
    summary = (sm.group(1) if sm else t.strip().split("\n")[-1] if t.strip() else "")[:400]
    if ap:
        return {"status": "needs_you", "summary": summary or ap.group(1)[:400], "approval": ap.group(1)[:300]}
    return {"status": "done", "summary": summary, "approval": ""}


def triage_pending(home: Path, *, llm: Callable[[List[Dict[str, str]]], str], spawn: Callable[[Dict[str, Any]], Optional[str]],
                   emit: Callable[[str, Dict[str, Any]], None], killed: Optional[bool] = None) -> List[str]:
    """Classify all untriaged items in one call, then start/draft eligible ones. Returns started ids."""
    import os

    data = store.load(home)
    pending = [i for i in data["items"] if not i.get("triaged") and not i.get("done")]
    if not pending:
        return []
    try:
        results = parse_triage(llm(build_prompt(pending)), pending)
    except Exception:
        results = {}
    kill = bool(os.environ.get(KILL_SWITCH_ENV)) if killed is None else killed
    started: List[str] = []
    for it in pending:
        tri = results.get(it["id"]) or heuristic(it)
        fields: Dict[str, Any] = {"triaged": True, "risk": tri["risk"], "plan": tri["plan"]}
        if not it.get("owner_set_by_user"):
            fields["owner"] = tri["owner"]
        if tri.get("notes") and not it.get("notes"):
            fields["notes"] = tri["notes"]
        owner_eff = fields.get("owner", it.get("owner"))
        eff = dict(tri, owner=owner_eff)
        settings = store.load(home).get("settings", {})
        verdict = decide(it, eff, settings, running_count(store.load(home)["items"]), killed=kill)
        if verdict == "start":
            sid = None
            try:
                sid = spawn(dict(it, **fields))
            except Exception:
                sid = None
            if sid:
                fields.update(session_id=sid, status="working", auto_started=True)
                started.append(it["id"])
        elif verdict == "draft":
            fields["status"] = "needs_you"
            fields["approval_needed"] = True
        updated = store.patch_system(home, it["id"], fields)
        if updated:
            emit("todos.changed", {"reason": "triage", "item": updated})
    return started


def finish_item(home: Path, item_id: str, final_text: str, emit: Callable[[str, Dict[str, Any]], None]) -> None:
    out = parse_outcome(final_text)
    fields: Dict[str, Any] = {"status": out["status"], "result_summary": out["summary"]}
    if out["status"] == "done":
        fields.update(done=True, done_at=time.time(), done_by="clover")
    else:
        fields["approval_needed"] = True
        fields["approval_text"] = out["approval"]
    it = store.patch_system(home, item_id, fields)
    if it:
        emit("todos.changed", {"reason": "finished", "item": it})


def fail_item(home: Path, item_id: str, emit: Callable[[str, Dict[str, Any]], None], error: str = "") -> None:
    it = store.patch_system(home, item_id, {"status": "failed", "result_summary": error[:300]})
    if it:
        emit("todos.changed", {"reason": "failed", "item": it})
