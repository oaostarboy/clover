"""The calm helper card: one summary line, then one row per helper.

Golden strings for a single helper, three helpers in mixed states, a failed
helper, a long task name, a crowd of helpers (Telegram limit) and the
Telegram wrapping.  The older long layout is covered by
test_ack_checklist_unified.py (style ``detailed``).
"""
import pytest

from agent import ack_checklist as ac
from agent import foreground_time_limit as fl

pytestmark = pytest.mark.rows_card

EM = "\u2003\u2003"
HEAD = "\U0001f504 working \u2192 something, ~5 min"


@pytest.fixture(autouse=True)
def _clean():
    ac.reset_for_tests()
    ac.set_style_for_tests("unified")
    yield
    ac.reset_for_tests()


def _card(n, did="d", say="", head=HEAD):
    r = fl.DispatchReceipt(delegation_id=did, seq=1, goals=tuple(f"g{i}" for i in range(n)),
                           summary=head, steps=("x",), say=say)
    return ac.card_for([r])


def _kw(did, i, n, **kw):
    return dict(delegation_id=did, task_index=i, task_count=n, subagent_id=f"sa-{i}", **kw)


def _start(did, i, n, title, age):
    ac.on_child_event("subagent.start", **_kw(did, i, n, model="claude-sonnet-5-5", title=title))
    return age


def _age(card, secs):
    for cl in (p.checklist for p in card.parts):
        for kid in cl.kids or [cl]:
            if kid.started is not None and kid.ended is None:
                kid.started -= secs[getattr(kid, "index", 0)] if cl.kids else secs[0]


def _three():
    card = _card(3, "dm", say="on it")
    ac.on_child_event("subagent.start", **_kw("dm", 0, 3, model="m", title="nice & tidy site"))
    ac.on_child_event("subagent.complete", status="completed", outcome="done",
                      summary="Plain summary: fine.", **_kw("dm", 0, 3, title="nice & tidy site"))
    ac.on_child_event("subagent.start", **_kw("dm", 1, 3, model="m", title="silas studios site"))
    ac.on_child_event("subagent.start", **_kw("dm", 2, 3, model="m", title="pizzeria bros pamphlet"))
    ac.on_child_event("subagent.complete", status="failed", plain_summary="the page was down",
                      **_kw("dm", 2, 3, title="pizzeria bros pamphlet"))
    kids = card.parts[0].checklist.kids
    kids[0].started, kids[0].ended = 100.0, 140.0
    kids[1].started = 1000.0
    kids[2].started, kids[2].ended = 100.0, 112.0
    return card


def _render(card, now=1072.0):
    cl = card.parts[0].checklist
    return cl.render_unified_multi(now=now, include_say=False)


def test_three_helpers_mixed_states():
    card = _three()
    assert _render(card).splitlines() == [
        "3 helpers \u00b7 1 working \u00b7 1 done \u00b7 1 failed",
        "> \u2713 nice & tidy site \u00b7 40s",
        "> \u25cf silas studios site \u00b7 1m12s",
        "> \u2715 pizzeria bros pamphlet \u00b7 failed \u00b7 12s",
        f"> {EM}*the page was down*",
    ]


def test_failed_is_obvious_and_has_its_own_mark():
    text = _render(_three())
    failed = [ln for ln in text.splitlines() if "\u2715" in ln]
    assert len(failed) == 1 and "failed" in failed[0]
    assert "\u2715" not in "".join(ln for ln in text.splitlines() if "failed" not in ln)


def test_one_helper_running():
    card = _card(1, "d1", say="on it")
    ac.on_child_event("subagent.start", delegation_id="d1", model="claude-sonnet-5-5")
    cl = card.parts[0].checklist
    cl.started = 1000.0
    lines = cl.render_unified(now=1007.0, include_say=False).splitlines()
    assert lines[0] == "1 helper \u00b7 working"
    assert lines[1].startswith("> \u25cf ") and lines[1].endswith("\u00b7 7s")


def test_no_model_goal_label_or_bold_or_tool_lines():
    ac.on_child_event("subagent.start", **_kw("dm", 1, 3, model="claude-sonnet-5-5", title="x"))
    text = _render(_three())
    for bad in ("Sonnet", "Goal:", "**", "\U0001f527", "\U0001f6e0", "\U0001f500", "\u23f1", "\u25b8"):
        assert bad not in text


def test_a_clover_emoji_never_appears_while_running():
    card = _three()
    live = _card(2, "dx")
    ac.on_child_event("subagent.start", **_kw("dx", 0, 2, model="m", title="a"))
    assert "\U0001f340" not in live.render()
    assert "\U0001f340" not in _render(card)


def test_a_long_task_name_is_cut_on_a_word_to_one_row():
    long = "Make the terminal welcome look polished with a real logo bigger and clean CLOVER letters"
    card = _card(1, "d1", head=f"\U0001f504 {long} \u2192 done, ~10\u201330 min")
    ac.on_child_event("subagent.start", delegation_id="d1", model="m")
    cl = card.parts[0].checklist
    row = cl.render_unified(now=cl.started + 5, include_say=False).splitlines()[1]
    assert row == "> \u25cf Make the terminal welcome look\u2026 \u00b7 5s"
    assert len(row) <= 2 + 2 + ac.ROW_TASK_CHARS + 8


def test_finished_all_done_leads_with_the_clover_mark():
    card = _card(2, "dm")
    for i in range(2):
        ac.on_child_event("subagent.start", **_kw("dm", i, 2, model="m", title=f"job {i}"))
        ac.on_child_event("subagent.complete", status="completed", outcome="done",
                          summary="Plain summary: ok.", **_kw("dm", i, 2, title=f"job {i}"))
    for k in card.parts[0].checklist.kids:
        k.started, k.ended = 10.0, 30.0
    lines = card.parts[0].checklist.render_unified_multi(now=99.0, include_say=False).splitlines()
    assert lines[0] == "\U0001f340 2 helpers \u00b7 2 done \u00b7 20s"
    assert lines[1:] == ["> \u2713 job 0 \u00b7 20s", "> \u2713 job 1 \u00b7 20s"]


def test_finished_with_a_failure_leads_with_the_failure_mark():
    card = _three()
    card.parts[0].checklist.kids[1].started = 1000.0
    ac.on_child_event("subagent.complete", status="completed", outcome="done",
                      summary="Plain summary: ok.", **_kw("dm", 1, 3, title="silas studios site"))
    kids = card.parts[0].checklist.kids
    kids[1].ended = 1030.0
    head = card.parts[0].checklist.render_unified_multi(now=2000.0, include_say=False).splitlines()[0]
    assert head.startswith("\U0001f940 3 helpers \u00b7 2 done \u00b7 1 failed")


def test_a_crowd_stays_under_the_telegram_limit_and_keeps_the_summary():
    n = 60
    card = _card(n, "big")
    for i in range(n):
        ac.on_child_event("subagent.start", **_kw("big", i, n, model="m",
                                                  title=f"task number {i} " + "word " * 12))
    for i in range(0, n, 2):
        ac.on_child_event("subagent.complete", status="failed",
                          plain_summary="it broke " * 8, **_kw("big", i, n, title=f"task number {i}"))
    text = card.parts[0].checklist.render_unified_multi(now=1e6, include_say=False)
    assert len(text) <= ac.CARD_BUDGET
    assert text.splitlines()[0].startswith(f"{n} helpers \u00b7 ")
    assert "more" in text.splitlines()[-1]


def test_rows_fold_keeps_failures_and_running_visible():
    n = ac.ROWS_SHOWN + 6
    card = _card(n, "fold")
    for i in range(n):
        ac.on_child_event("subagent.start", **_kw("fold", i, n, model="m", title=f"t{i}"))
    for i in range(6):
        ac.on_child_event("subagent.complete", status="completed", outcome="done",
                          summary="Plain summary: ok.", **_kw("fold", i, n, title=f"t{i}"))
    text = card.parts[0].checklist.render_unified_multi(now=1e6, include_say=False)
    assert "+6 done" in text and "t6" in text and "t0 " not in text


def test_telegram_keeps_the_rows_visible_not_collapsed():
    from gateway import delegation_activity as da

    card = _three()
    pub = da.AckCardEditor.__new__(da.AckCardEditor)
    pub.card = card
    pub._expandable = True
    card.split_opener = True
    text = pub._render()
    assert "**>" not in text and "||" not in text and "tap to" not in text
    assert text.splitlines()[0].startswith("3 helpers")
    assert len(text) < 4000


def test_detailed_style_still_available():
    ac.set_style_for_tests("detailed")
    card = _three()
    assert "Goal:" in card.parts[0].checklist.render_unified_multi(now=2000.0) or "**" in card.render()
