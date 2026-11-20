"""A helper that never touches its checklist gets one reminder per N calls."""
from types import SimpleNamespace

from agent.agent_runtime_helpers import (
    STALE_CHECKLIST_NOTE, apply_pending_steer_to_tool_results)
from agent import ack_checklist as ack
from tools.todo_tool import TodoStore


def _agent(depth=1, items=True):
    store = TodoStore()
    if items:
        store.write([{"id": "s1", "content": "a", "status": "in_progress"},
                     {"id": "s2", "content": "b", "status": "pending"}])
    a = SimpleNamespace(_delegate_depth=depth, _todo_store=store,
                        _stale_ck_threshold=8)
    a._drain_pending_steer = lambda: None
    return a


def _run(agent, n, msgs=None):
    msgs = msgs if msgs is not None else [{"role": "user", "content": "go"}]
    hits = []
    for i in range(n):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(i)}]})
        msgs.append({"role": "tool", "tool_call_id": str(i), "content": f"r{i}"})
        before = len(msgs)
        apply_pending_steer_to_tool_results(agent, msgs, 1)
        assert len(msgs) == before
        if msgs[-1]["content"] != f"r{i}":
            hits.append(i + 1)
    return msgs, hits


def test_one_reminder_after_eight_calls():
    msgs, hits = _run(_agent(), 10)
    assert hits == [8]
    assert [m["role"] for m in msgs].count("user") == 1
    assert all(m["role"] != "system" for m in msgs)
    assert "checklist hasn't changed" in msgs[16]["content"]
    assert STALE_CHECKLIST_NOTE.startswith("Note:")


def test_repeats_every_n():
    assert _run(_agent(), 17)[1] == [8, 16]


def test_top_level_agent_never_reminded():
    assert _run(_agent(depth=0), 20)[1] == []


def test_no_open_items_no_reminder():
    a = _agent()
    a._todo_store.write([{"id": "s1", "content": "a", "status": "completed"},
                         {"id": "s2", "content": "b", "status": "completed"}])
    assert _run(a, 20)[1] == []


def test_todo_update_resets_counter():
    a = _agent()
    msgs, _ = _run(a, 6)
    a._todo_store.write([{"id": "s1", "content": "a", "status": "completed"}], merge=True)
    assert _run(a, 7, msgs)[1] == []
    assert _run(a, 1, msgs)[1] == [1]


def test_done_closeout_completes_all_rows():
    rows = [["a", "in_progress"], ["b", "pending"], ["c", "pending"]]
    ack._finish_steps_for(rows, ack.DONE)
    assert {r[1] for r in rows} == {"completed"}


def test_partial_closeout_never_running_or_completed_tail():
    for state in (ack.PARTIAL, ack.BLOCKED, ack.FAILED, ack.STOPPED):
        rows = [["a", "completed"], ["b", "in_progress"], ["c", "pending"]]
        ack._finish_steps_for(rows, state)
        assert rows[0][1] == "completed"
        assert rows[1][1] not in ("in_progress", "pending", "completed")
        assert rows[2][1] in ("pending", "cancelled")


def test_config_default():
    from clover_cli.config_defaults import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["delegation"]["checklist_reminder_calls"] == 8


def test_card_text_after_finish():
    cl = ack.Checklist.start("h", ["one", "two", "three"])
    cl.finish(ack.DONE)
    assert all(st == "completed" for _, st in cl.steps)
    cl = ack.Checklist.start("h", ["one", "two", "three"])
    cl.finish(ack.PARTIAL)
    assert [st for _, st in cl.steps] == ["unfinished", "pending", "pending"]
    assert ack.ACTIVE_ICON not in cl.render()
