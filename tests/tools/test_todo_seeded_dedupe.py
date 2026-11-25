"""A helper re-adding a seeded launch step under a new id updates that step."""
import json
import pytest
from agent import ack_checklist as ac
from tools.todo_tool import TodoStore, todo_tool

pytestmark = pytest.mark.unified_card
STEPS = ["check the planner", "merge it", "release", "move Octavia"]


def _seeded():
    s = TodoStore()
    s.write(ac.seed_todos(STEPS))
    return s


def test_child_reuse_of_seeded_text_keeps_four_rows():
    s = _seeded()
    items = s.write([{"id": "1", "content": " Check the  PLANNER ", "status": "in_progress"}], merge=True)
    assert [i["id"] for i in items] == ["s1", "s2", "s3", "s4"]
    assert items[0]["status"] == "in_progress"


def test_result_tells_child_the_seeded_ids():
    s = _seeded()
    out = json.loads(todo_tool(todos=[{"id": "s1", "content": "check the planner", "status": "completed"}], merge=True, store=s))
    assert "s1..s4" in out["note"] and "by id" in out["note"]


def test_card_has_no_duplicate_lines():
    ac.reset_for_tests(); ac.set_style_for_tests("unified")
    steps = [[t, "in_progress" if i == 0 else "pending"] for i, t in enumerate(STEPS)]
    ids = ["s1", "s2", "s3", "s4"]
    todos = [{"id": "s1", "content": STEPS[0], "status": "in_progress"},
             {"id": "s2", "content": STEPS[1], "status": "pending"},
             {"id": "s3", "content": STEPS[2], "status": "pending"},
             {"id": "s4", "content": STEPS[3], "status": "pending"},
             {"id": "1", "content": STEPS[0], "status": "in_progress"}]
    ac._apply_todos_to(steps, todos, ids)
    assert [r[0] for r in steps] == STEPS
    assert steps[0][1] == "in_progress"
    ac.reset_for_tests()


def test_rendered_card_text_has_no_duplicate_line():
    from agent import foreground_time_limit as fl
    ac.reset_for_tests(); ac.set_style_for_tests("unified")
    r = fl.DispatchReceipt(delegation_id="d", seq=1, goals=("Fix the gallery",), summary="\U0001f504 fixing the bugs \u2192 test build", steps=tuple(STEPS), say="")
    card = ac.card_for([r])
    ac.on_child_event("subagent.todo", delegation_id="d", todos=[
        {"id": "1", "content": STEPS[0], "status": "in_progress"}])
    ac.on_child_event("subagent.todo", delegation_id="d", todos=[
        {"id": f"s{i+1}", "content": t, "status": "in_progress" if i == 0 else "pending"} for i, t in enumerate(STEPS)
    ] + [{"id": "1", "content": STEPS[0], "status": "in_progress"}])
    texts = [ln[2:] for ln in card.render().splitlines()
             if ln[:3] in ("> \u2713", "> \u25b8", "> \u25cb", "> \u2717", "> \u25a0")]
    assert len(texts) == len(set(texts)) == 4
    ac.reset_for_tests()
