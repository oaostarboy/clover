"""Owner to-do list JSON-RPC (``todos.*``) for the phone/desktop Checklist tab.

Handlers are rebound onto server.py globals at install time (method_ctx.py).
Item shape: ``{id, text, notes, done, done_at, created_at, updated_at, order,
due_at, remind_at, owner(clover|you|together|null), risk, plan, status
(''|queued|working|needs_you|done|failed), session_id, result_summary,
approval_needed, approval_text, done_by}``.  Every mutation broadcasts
``todos.changed`` ``{reason, item?|id?|items?}`` to all connected clients.
"""

from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()
method = _registry.method


@method("todos.list")
def _(rid, params: dict) -> dict:
    from tui_gateway import todo_runtime, todo_store

    home = get_clover_home()
    try:
        todo_runtime.reconcile_interrupted(home)  # runs whose thread died with a restart -> failed
    except Exception:
        pass
    return _ok(rid, todo_store.listing(home))


@method("todos.add")
def _(rid, params: dict) -> dict:
    from tui_gateway import todo_runtime, todo_store

    text = params.get("text")
    if not isinstance(text, str) or not text.strip():
        return _err(rid, 4002, "text required")
    from tui_gateway import todo_triage

    assignee, bad = todo_triage.resolve_assignee(params.get("assignee"))
    if assignee is None:
        return _err(rid, 4002, bad)
    home = get_clover_home()
    created = todo_store.add(home, text, assignee=assignee)
    if created:
        _broadcast_global_event("todos.changed", {"reason": "added", "items": created})
        todo_runtime.kick(home)
    return _ok(rid, {"items": created})


@method("todos.update")
def _(rid, params: dict) -> dict:
    from tui_gateway import todo_store

    from tui_gateway import todo_runtime, todo_triage

    item_id = str(params.get("id") or "")
    patch = {k: params[k] for k in ("done", "text", "notes", "due_at", "remind_at", "owner", "assignee") if k in params}
    if "assignee" in patch:
        a, bad = todo_triage.resolve_assignee(patch["assignee"])
        if a is None:
            return _err(rid, 4002, bad)
        patch["assignee"] = a
    it = todo_store.update(get_clover_home(), item_id, patch)
    if it is None:
        return _err(rid, 4004, "unknown todo")
    _broadcast_global_event("todos.changed", {"reason": "updated", "item": it})
    if "assignee" in patch:
        todo_runtime.kick(get_clover_home())  # re-triage / re-dispatch under the new assignee
    return _ok(rid, {"item": it})


@method("todos.delete")
def _(rid, params: dict) -> dict:
    from tui_gateway import todo_store

    item_id = str(params.get("id") or "")
    if not todo_store.delete(get_clover_home(), item_id):
        return _err(rid, 4004, "unknown todo")
    _broadcast_global_event("todos.changed", {"reason": "deleted", "id": item_id})
    return _ok(rid, {"deleted": True})


@method("todos.reorder")
def _(rid, params: dict) -> dict:
    from tui_gateway import todo_store

    ids = params.get("ids")
    if not isinstance(ids, list):
        return _err(rid, 4002, "ids required")
    items = todo_store.reorder(get_clover_home(), [str(i) for i in ids])
    _broadcast_global_event("todos.changed", {"reason": "reordered", "items": items})
    return _ok(rid, {"items": items})


@method("todos.settings")
def _(rid, params: dict) -> dict:
    """Get/set ``auto_start``.  Turning it on stamps ``enabled_at`` so older items never auto-start."""
    import time

    from tui_gateway import todo_store

    home = get_clover_home()
    data = todo_store.load(home)
    s = data.setdefault("settings", {})
    if "auto_start" in params:
        if not isinstance(params["auto_start"], bool):
            return _err(rid, 4002, "auto_start must be true or false")
        want = params["auto_start"]
        if want and not s.get("auto_start", True):
            s["enabled_at"] = time.time()
        s["auto_start"] = want
        todo_store.save(home, data)
        _broadcast_global_event("todos.changed", {"reason": "settings", "settings": s})
    return _ok(rid, {"settings": s})


@method("todos.approve")
def _(rid, params: dict) -> dict:
    """Owner taps Approve on a drafted/external item: clears the gate and opens the item's chat to proceed."""
    from tui_gateway import todo_store

    from tui_gateway import todo_runtime, todo_triage

    item_id = str(params.get("id") or "")
    home = get_clover_home()
    cur = next((i for i in todo_store.load(home)["items"] if i["id"] == item_id), None)
    if cur and cur.get("waiting_for_you") and cur.get("waiting_agent") and not cur.get("done"):
        # Owner tapped Start on an item queued for another agent: dispatch now (roster re-checked inside assign).
        agent = str(cur["waiting_agent"])
        base = {"waiting_for_you": False, "waiting_agent": "", "approval_needed": False, "approved": True}
        todo_store.patch_system(home, item_id, dict(base, status="queued"))  # claim first: a double tap can't dispatch twice
        if todo_triage.kill_switch_on():
            it = todo_store.patch_system(home, item_id, dict(base, status="failed", result_summary="Auto-start is switched off"))
        else:
            sid, err = todo_runtime.assign(cur, agent)
            fields = dict(base, status="working", session_id=sid, auto_started=True) if sid else dict(
                base, status="failed", result_summary=(err or "Could not reach that agent")[:300])
            it = todo_store.patch_system(home, item_id, fields)
        _broadcast_global_event("todos.changed", {"reason": "approved", "item": it})
        return _ok(rid, {"item": it})
    it = todo_store.patch_system(home, item_id, {"approval_needed": False, "status": "queued", "owner": "clover", "approved": True})
    if it is None:
        return _err(rid, 4004, "unknown todo")
    _broadcast_global_event("todos.changed", {"reason": "approved", "item": it})
    return _ok(rid, {"item": it})


def register(server) -> None:
    _registry.install(server)
