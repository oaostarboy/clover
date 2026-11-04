"""Optional to-do assignee: validation, dispatch to a chosen agent, and the risk gate still applying."""
import json

import pytest

from tui_gateway import todo_store as st
from tui_gateway import todo_triage as tr


def _open(tmp_path, text, assignee):
    made = st.add(tmp_path, text, assignee=assignee)
    data = st.load(tmp_path)
    data["settings"]["enabled_at"] = 0
    st.save(tmp_path, data)
    return made[0]


def _low(item):
    return json.dumps([{"id": item["id"], "owner": "you", "risk": "low", "plan": "p"}])


@pytest.mark.parametrize("raw,want", [(None, "auto"), ("", "auto"), ("AUTO", "auto"), ("Owner", "owner"),
                                      ("octavia", "octavia"), (" suni ", "suni"), (5, None), ("a b", None),
                                      ("../x", None), ("x" * 90, None)])
def test_normalize_assignee(raw, want):
    assert tr.normalize_assignee(raw) == want


def test_default_assignee_is_auto_and_update_reopens_dispatch(tmp_path):
    it = st.add(tmp_path, "research monitors")[0]
    assert it["assignee"] == "auto"
    st.patch_system(tmp_path, it["id"], {"triaged": True, "status": "failed"})
    upd = st.update(tmp_path, it["id"], {"assignee": "suni"})
    assert upd["assignee"] == "suni" and upd["triaged"] is False and upd["status"] == ""


def test_default_agent_dispatches_immediately(tmp_path):
    it = _open(tmp_path, "research ultrawide monitors", "default")
    calls = []
    started = tr.triage_pending(
        tmp_path, llm=lambda m: _low(it), spawn=lambda i: pytest.fail("must not use the local spawn"),
        emit=lambda *a: None, killed=False,
        assign=lambda i, agent: calls.append((i["id"], agent)) or ("sess-9", ""))
    assert started == [it["id"]] and calls == [(it["id"], "default")]
    row = st.load(tmp_path)["items"][0]
    assert row["owner"] == "clover" and row["status"] == "working" and row["session_id"] == "sess-9"


def test_unknown_or_offline_agent_is_a_clear_error_status(tmp_path):
    it = _open(tmp_path, "research ultrawide monitors", "default")
    tr.triage_pending(tmp_path, llm=lambda m: _low(it), spawn=lambda i: "x", emit=lambda *a: None, killed=False,
                      assign=lambda i, a: (None, f"Unknown agent '{a}'"))
    row = st.load(tmp_path)["items"][0]
    assert row["status"] == "failed" and "Unknown agent 'default'" in row["result_summary"]
    # a raising dispatcher is contained too
    it2 = _open(tmp_path, "research b", "default")
    def boom(i, a): raise RuntimeError("peer down")
    tr.triage_pending(tmp_path, llm=lambda m: _low(it2), spawn=lambda i: "x", emit=lambda *a: None, killed=False, assign=boom)
    by = {i["id"]: i for i in st.load(tmp_path)["items"]}
    assert by[it2["id"]]["status"] == "failed" and "peer down" in by[it2["id"]]["result_summary"]


@pytest.mark.parametrize("text", ["email the dentist", "buy milk", "delete old files", "post this on x"])
def test_risk_gate_still_applies_to_assigned_agents(tmp_path, text):
    it = _open(tmp_path, text, "octavia")
    reply = json.dumps([{"id": it["id"], "owner": "clover", "risk": "low", "external": False}])
    tr.triage_pending(tmp_path, llm=lambda m: reply, spawn=lambda i: "x", emit=lambda *a: None, killed=False,
                      assign=lambda i, a: pytest.fail("a consequential item must never be dispatched"))
    row = st.load(tmp_path)["items"][0]
    assert row["status"] == "needs_you" and row["approval_needed"] is True and not row["session_id"]


def test_kill_switch_and_autostart_off_hold_agent_items(tmp_path):
    it = _open(tmp_path, "research x", "octavia")
    tr.triage_pending(tmp_path, llm=lambda m: _low(it), spawn=lambda i: "x", emit=lambda *a: None, killed=True,
                      assign=lambda i, a: pytest.fail("kill switch must hold agent dispatch"))
    assert st.load(tmp_path)["items"][0]["status"] == ""


def test_owner_assignee_is_never_classified_or_started(tmp_path):
    it = _open(tmp_path, "research monitors", "owner")
    out = tr.triage_pending(tmp_path, llm=lambda m: pytest.fail("no model call for owner items"),
                            spawn=lambda i: pytest.fail("no spawn"), emit=lambda *a: None, killed=False,
                            assign=lambda i, a: pytest.fail("no assign"))
    row = st.load(tmp_path)["items"][0]
    assert out == [] and row["owner"] == "you" and row["triaged"] is True and row["status"] == ""


def test_runtime_assign_validates_roster(monkeypatch, tmp_path):
    from tui_gateway import methods_crew, todo_runtime

    roster = [{"id": "default", "display_name": "Clover", "kind": "profile", "online": True},
              {"id": "octavia", "display_name": "Octavia", "kind": "peer", "online": False},
              {"id": "suni", "display_name": "Suni", "kind": "peer", "online": True}]
    monkeypatch.setattr(methods_crew, "crew_roster", lambda: roster)
    item = {"id": "i1", "text": "research x", "notes": "", "plan": ""}
    assert todo_runtime.assign(item, "nobody") == (None, "Unknown agent 'nobody'")
    assert todo_runtime.assign(item, "octavia") == (None, "Octavia is offline")
    monkeypatch.setattr(todo_runtime, "spawn", lambda i: "local-sess")
    assert todo_runtime.assign(item, "default") == ("local-sess", "")


def test_runtime_assign_peer_uses_restricted_prompt_via_relay(monkeypatch, tmp_path):
    import threading
    from tui_gateway import crew, methods_crew, server, todo_runtime

    monkeypatch.setattr(methods_crew, "crew_roster", lambda: [{"id": "suni", "display_name": "Suni", "kind": "peer", "online": True}])
    monkeypatch.setattr(server, "get_clover_home", lambda: tmp_path)
    sent, done = [], threading.Event()
    def relay(peer, text, **kw):
        sent.append((peer, text, kw)); return {"peer": peer, "session_id": "s", "reply": "ok\nSUMMARY: found it"}
    monkeypatch.setattr(crew, "relay_turn", relay)
    monkeypatch.setattr(todo_runtime, "broadcast", lambda e, p: done.set())
    it = st.add(tmp_path, "research monitors", assignee="suni")[0]
    sid, err = todo_runtime.assign(it, "suni")
    assert sid.startswith("todo-") and err == ""
    assert done.wait(5)
    peer, text, kw = sent[0]
    assert peer == "suni" and "Do NOT send, post, buy, delete" in text and "web search and page reading only" in text
    assert st.load(tmp_path)["items"][0]["done_by"] == "clover"


def test_rpc_assignee_validation(tmp_path, monkeypatch):
    from tui_gateway import methods_crew, server, todo_runtime

    monkeypatch.setattr(methods_crew, "crew_roster", lambda: [{"id": "suni"}])
    monkeypatch.setattr(server, "get_clover_home", lambda: tmp_path)
    monkeypatch.setattr(todo_runtime, "kick", lambda home: None)
    monkeypatch.setattr(server, "_broadcast_global_event", lambda e, p=None: None)
    assert "error" in server._methods["todos.add"](1, {"text": "x", "assignee": "bad id"})
    r = server._methods["todos.add"](2, {"text": "research x", "assignee": "suni"})
    item = r["result"]["items"][0]
    assert item["assignee"] == "suni"
    assert "error" in server._methods["todos.update"](3, {"id": item["id"], "assignee": 7})
    assert server._methods["todos.update"](4, {"id": item["id"], "assignee": "owner"})["result"]["item"]["assignee"] == "owner"


def test_other_agent_waits_for_owner_start(tmp_path):
    it = _open(tmp_path, "research ultrawide monitors", "octavia")
    started = tr.triage_pending(
        tmp_path, llm=lambda m: _low(it), spawn=lambda i: pytest.fail("no spawn"), emit=lambda *a: None, killed=False,
        assign=lambda i, a: pytest.fail("peer must not start before the owner taps Start"))
    row = st.load(tmp_path)["items"][0]
    assert started == [] and row["status"] == "needs_you" and row["waiting_for_you"] is True
    assert row["waiting_agent"] == "octavia" and not row["session_id"]


def test_approve_starts_waiting_agent_once_and_kill_switch_holds(monkeypatch, tmp_path):
    from tui_gateway import server, todo_runtime

    monkeypatch.setattr(server, "get_clover_home", lambda: tmp_path)
    monkeypatch.setattr(server, "_broadcast_global_event", lambda e, p=None: None)
    calls = []
    monkeypatch.setattr(todo_runtime, "assign", lambda item, agent: calls.append(agent) or ("todo-1", ""))
    it = st.add(tmp_path, "research x", assignee="suni")[0]
    st.patch_system(tmp_path, it["id"], {"waiting_for_you": True, "waiting_agent": "suni", "status": "needs_you", "approval_needed": True})
    monkeypatch.setenv("CLOVER_TODOS_AUTOSTART_DISABLED", "1")
    r = server._methods["todos.approve"](1, {"id": it["id"]})["result"]["item"]
    assert r["status"] == "failed" and calls == []
    st.patch_system(tmp_path, it["id"], {"waiting_for_you": True, "waiting_agent": "suni", "status": "needs_you"})
    monkeypatch.delenv("CLOVER_TODOS_AUTOSTART_DISABLED")
    r = server._methods["todos.approve"](2, {"id": it["id"]})["result"]["item"]
    assert r["status"] == "working" and r["session_id"] == "todo-1" and calls == ["suni"]
    server._methods["todos.approve"](3, {"id": it["id"]})
    assert calls == ["suni"]  # second tap does not re-dispatch


def test_rpc_rejects_assignee_not_in_roster(monkeypatch, tmp_path):
    from tui_gateway import methods_crew, server, todo_runtime

    monkeypatch.setattr(server, "get_clover_home", lambda: tmp_path)
    monkeypatch.setattr(todo_runtime, "kick", lambda home: None)
    monkeypatch.setattr(server, "_broadcast_global_event", lambda e, p=None: None)
    monkeypatch.setattr(methods_crew, "crew_roster", lambda: [{"id": "Suni"}])
    assert "error" in server._methods["todos.add"](1, {"text": "x", "assignee": "nobody"})
    assert "error" in server._methods["todos.add"](2, {"text": "x", "assignee": "../etc"})
    r = server._methods["todos.add"](3, {"text": "research x", "assignee": "suni"})
    assert r["result"]["items"][0]["assignee"] == "Suni"
