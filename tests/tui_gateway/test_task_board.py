"""delegation.status additions for the phone Tasks tab: live checklists, finished list, needs-you."""

from __future__ import annotations

import threading

from tui_gateway import task_board as tb


def _record():
    return {
        "delegation_id": "deleg_1",
        "tasks": [
            {"goal": "a", "title": "Build tabs", "checklist": [
                {"id": "s1", "content": "Read code", "status": "completed"},
                {"id": "s2", "content": "Mocks", "status": "in_progress"},
                {"id": "s3", "content": "Tests", "status": "pending"},
                {"id": "x", "content": "", "status": "pending"},
                {"id": "y", "content": "Weird", "status": "bogus"},
            ]},
            {"goal": "second goal", "title": "", "checklist": []},
        ],
        "handoff": {"work": "Whole job"},
    }


def test_task_index_of():
    assert tb.task_index_of("sa-0-abcd1234") == 0
    assert tb.task_index_of("sa-12-ff") == 12
    assert tb.task_index_of("idx3") is None
    assert tb.task_index_of(None) is None


def test_enrich_active_attaches_checklist_and_progress():
    active = [
        {"subagent_id": "sa-0-aaaa", "delegation_id": "deleg_1", "goal": "g0", "model": "m", "started_at": 1.0},
        {"subagent_id": "sa-1-bbbb", "delegation_id": "deleg_1", "goal": "second goal", "model": "m", "started_at": 2.0},
        {"subagent_id": "sa-0-cccc", "delegation_id": None, "goal": "orphan goal", "model": "m", "started_at": 3.0},
    ]
    calls = []

    def reader(did):
        calls.append(did)
        return _record()

    out = tb.enrich_active(active, read_record=reader)
    assert calls == ["deleg_1"]  # one read per delegation
    a, b, c = out
    assert a["title"] == "Build tabs"
    assert [s["status"] for s in a["checklist"]] == ["completed", "in_progress", "pending", "pending"]
    assert (a["done_steps"], a["total_steps"]) == (1, 4)
    assert b["title"] == "Whole job" and b["checklist"] == [] and b["total_steps"] == 0
    assert c["title"] == "orphan goal" and c["checklist"] == []
    assert "checklist" not in active[0]  # input not mutated


def test_enrich_missing_record_falls_back_to_goal():
    out = tb.enrich_active([{"subagent_id": "sa-0-a", "delegation_id": "d", "goal": "  do   the thing "}], read_record=lambda d: None)
    assert out[0]["title"] == "do the thing" and out[0]["checklist"] == []


def test_recent_done_bounded_newest_first():
    tb._RECENT.clear()
    for i in range(50):
        tb.note_finished({"subagent_id": f"sa-{i}-x", "goal": f"job {i}", "result_line": "ok"}, "done" if i % 2 else "failed")
    items = tb.recent_done(limit=3)
    assert [x["title"] for x in items] == ["job 49", "job 48", "job 47"]
    assert len(tb.recent_done(limit=1000)) == 40
    assert tb.recent_done(limit=1)[0]["outcome"] == "done"
    tb.note_finished({"subagent_id": "z", "goal": "g"}, "weird")
    assert tb.recent_done(limit=1)[0]["outcome"] == "done"


def test_needs_you_snapshot_reports_approval_and_clarify(monkeypatch):
    import sys
    import types

    fake_approval = types.SimpleNamespace(
        get_pending_gateway_approval=lambda key: {"request_id": "r1", "description": "rm -rf?", "command": "rm x"} if key == "k1" else None)
    monkeypatch.setitem(sys.modules, "tools.approval", fake_approval)
    clarify = lambda sid: {"request_id": "c1", "question": "Which one?", "choices": ["a", "b"]} if sid == "s2" else None
    sessions = {
        "s1": {"session_key": "k1", "title": "Release"},
        "s2": {"session_key": "k2", "title": "Ask"},
        "s3": {"session_key": "k1", "_finalized": True},
    }
    items = tb.needs_you_snapshot(sessions, threading.RLock(), clarify)
    assert [(i["type"], i["session_id"], i["request_id"]) for i in items] == [("approval", "s1", "r1"), ("clarify", "s2", "c1")]
    assert items[0]["choices"] == ["once", "deny"] and items[1]["choices"] == ["a", "b"]
