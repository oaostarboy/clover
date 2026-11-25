"""A helper card always has a checklist, and it follows the helper's own todos."""
from __future__ import annotations

import pytest

from agent import ack_checklist as ac
from agent import foreground_time_limit as fl

pytestmark = pytest.mark.unified_card
HEAD = "\U0001f504 fixing the bugs \u2192 test build"


@pytest.fixture(autouse=True)
def _clean():
    ac.reset_for_tests()
    ac.set_style_for_tests("detailed")
    yield
    ac.reset_for_tests()


def _card(did="d", goals=("Fix the gallery",), steps=()):
    r = fl.DispatchReceipt(delegation_id=did, seq=1, goals=tuple(goals), summary=HEAD, steps=tuple(steps), say="")
    return ac.card_for([r])


def _todo(did, *rows, **kw):
    ac.on_child_event("subagent.todo", delegation_id=did, **kw,
                      todos=[{"id": i, "content": c, "status": s} for i, c, s in rows])


def _rows(card):
    return [ln[2:] for ln in card.render().splitlines() if ln[:3] in ("> \u2713", "> \u25b8", "> \u25cb", "> \u2717", "> \u25a0")]


def test_launch_without_steps_still_has_a_checklist_row():
    assert _rows(_card()) == ["\u25b8 Fix the gallery"]


def test_child_todos_replace_goal_row_and_follow_in_order():
    card = _card()
    _todo("d", ("a", "Open gallery", "in_progress"), ("b", "Swap picture", "pending"), ("c", "Check", "pending"))
    assert _rows(card) == ["\u25b8 Open gallery", "\u25cb Swap picture", "\u25cb Check"]
    _todo("d", ("a", "Open gallery", "completed"), ("b", "Swap picture", "in_progress"), ("c", "Check", "pending"))
    assert _rows(card) == ["\u2713 Open gallery", "\u25b8 Swap picture", "\u25cb Check"]
    _todo("d", ("a", "Open gallery", "completed"), ("b", "Swap picture", "completed"), ("c", "Check", "in_progress"))
    assert _rows(card) == ["\u2713 Open gallery", "\u2713 Swap picture", "\u25b8 Check"]


def test_launch_steps_with_different_child_labels_leave_no_stale_rows():
    card = _card(steps=("find", "fix", "test"))
    _todo("d", ("x1", "Locate bug", "completed"), ("x2", "Patch it", "in_progress"))
    assert _rows(card) == ["\u2713 Locate bug", "\u25b8 Patch it"]


def test_launch_step_matched_by_label_is_kept_not_duplicated():
    card = _card(steps=("find", "fix"))
    _todo("d", ("a", "find", "completed"), ("b", "fix", "in_progress"), ("c", "ship", "pending"))
    assert _rows(card) == ["\u2713 find", "\u25b8 fix", "\u25cb ship"]


def test_never_two_rows_running():
    card = _card()
    _todo("d", ("a", "one", "in_progress"), ("b", "two", "in_progress"))
    assert _rows(card) == ["\u25b8 one", "\u25cb two"]


def test_completion_settles_and_late_update_is_rejected():
    card = _card()
    _todo("d", ("a", "Open", "completed"), ("b", "Swap", "in_progress"))
    ac.on_child_event("subagent.complete", delegation_id="d", status="completed", summary="done")
    settled = card.render()
    assert "\u25b8" not in settled and "\u25cb" not in settled
    _todo("d", ("a", "Open", "in_progress"), ("z", "Late row", "in_progress"))
    assert card.render() == settled


def test_siblings_do_not_mix_rows():
    card = ac.open_dispatch_card("db", ["Task one", "Task two"], titles=["One", "Two"])
    _todo("db", ("a", "A1", "in_progress"), ("a2", "A2", "pending"), task_index=0)
    _todo("db", ("b", "B1", "in_progress"), task_index=1)
    cl = card.parts[0].checklist
    assert [r[0] for r in cl.kids[0].steps] == ["A1", "A2"]
    assert [r[0] for r in cl.kids[1].steps] == ["B1"]
    ac.on_child_event("subagent.complete", delegation_id="db", task_index=0, status="completed", summary="x")
    _todo("db", ("b", "B1", "completed"), task_index=1)
    assert [r[1] for r in cl.kids[1].steps] == ["completed"]


def test_serialized_telegram_text_follows_the_helper():
    from gateway.platforms.base import BasePlatformAdapter  # noqa: F401
    from plugins.platforms.telegram.adapter import TelegramAdapter
    fm = lambda t: TelegramAdapter.format_message(object.__new__(TelegramAdapter), t)
    card = _card()
    before = fm(card.render())
    _todo("d", ("a", "Open gallery", "completed"), ("b", "Swap picture", "in_progress"))
    after = fm(card.render())
    assert "Swap picture" in after and "Open gallery" in after
    assert "Fix the gallery" not in after
    assert "Fix the gallery" in before
    assert after.count("\u25b8") == 1
