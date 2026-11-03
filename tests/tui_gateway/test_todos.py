"""Owner to-do list: store, splitting, dates, triage parsing, risk gate, events."""
import json
from datetime import datetime

import pytest

from tui_gateway import todo_store as st
from tui_gateway import todo_triage as tr

NOW = datetime(2026, 11, 3, 10, 0)  # Tuesday


def test_split_commas_newlines_bullets():
    assert st.split_items("buy milk, book dentist, research ultrawide monitors") == [
        "buy milk", "book dentist", "research ultrawide monitors"]
    assert st.split_items("- a\n* b\n1. c\n\n[ ] d") == ["a", "b", "c", "d"]
    assert st.split_items("dinner with Sam, Alex") == ["dinner with Sam, Alex"]
    assert st.split_items("a\na") == ["a"]


def test_parse_when():
    t, due = st.parse_when("gym tomorrow 6:30pm", NOW)
    assert t == "gym" and datetime.fromtimestamp(due) == datetime(2026, 11, 4, 18, 30)
    t, due = st.parse_when("call mom friday", NOW)
    assert t == "call mom" and datetime.fromtimestamp(due).date() == datetime(2026, 11, 6).date()
    t, due = st.parse_when("pay rent Nov 5", NOW)
    assert datetime.fromtimestamp(due).date() == datetime(2026, 11, 5).date()
    t, due = st.parse_when("stretch in 2 hours", NOW)
    assert t == "stretch" and datetime.fromtimestamp(due) == datetime(2026, 11, 3, 12, 0)
    assert st.parse_when("buy milk", NOW) == ("buy milk", None)


def test_crud_reorder_persist(tmp_path):
    made = st.add(tmp_path, "a, b, c")
    assert [i["text"] for i in made] == ["a", "b", "c"]
    a, b, c = made
    st.reorder(tmp_path, [c["id"], a["id"]])
    assert [i["text"] for i in st.listing(tmp_path)["items"]] == ["c", "a", "b"]
    it = st.update(tmp_path, a["id"], {"done": True})
    assert it["done"] and it["done_at"] and it["done_by"] == "you"
    assert st.update(tmp_path, a["id"], {"done": False})["done"] is False
    assert st.update(tmp_path, "nope", {"done": True}) is None
    assert st.delete(tmp_path, b["id"]) and not st.delete(tmp_path, b["id"])
    assert len(json.loads((tmp_path / "todos.json").read_text())["items"]) == 2


def _items(*texts):
    return [{"id": f"i{n}", "text": t, "created_at": 100.0} for n, t in enumerate(texts)]


def test_parse_triage_validates():
    items = _items("research monitors", "email boss")
    raw = 'sure ```json [{"id":"i0","owner":"clover","risk":"low","plan":"search"},' \
          '{"id":"i1","owner":"clover","risk":"low","external":false},{"id":"zz","owner":"you"}]```'
    out = tr.parse_triage(raw, items)
    assert set(out) == {"i0", "i1"}
    assert out["i0"]["risk"] == "low" and not out["i0"]["external"]
    # model said low/not external but text is external -> forced high
    assert out["i1"]["external"] and out["i1"]["risk"] == "high"
    assert tr.parse_triage("garbage", items) == {}
    assert tr.parse_triage('[{"id":"i0","owner":"bogus","risk":"x"}]', items)["i0"] == {
        "owner": "you", "risk": "high", "external": False, "plan": "", "notes": ""}


S = {"auto_start": True, "enabled_at": 50.0}


def test_gate_external_never_starts():
    for text in ["email the landlord", "buy a monitor", "delete old backups", "post update on X", "invite Sam to lunch"]:
        it = {"text": text, "created_at": 100.0}
        v = tr.decide(it, {"owner": "clover", "risk": "low", "external": False}, S, 0)
        assert v in ("draft", "hold") and v != "start", text


def test_gate_conditions():
    it = {"text": "research ultrawide monitors", "created_at": 100.0}
    low = {"owner": "clover", "risk": "low", "external": False}
    assert tr.decide(it, low, S, 0) == "start"
    assert tr.decide(it, low, S, 2) == "hold"                      # rate limit
    assert tr.decide(it, low, {"auto_start": False, "enabled_at": 0}, 0) == "hold"
    assert tr.decide(it, low, S, 0, killed=True) == "hold"          # kill switch
    assert tr.decide(dict(it, created_at=10.0), low, S, 0) == "hold"  # older than setting
    assert tr.decide(it, dict(low, risk="medium"), S, 0) == "hold"
    assert tr.decide(it, dict(low, owner="you"), S, 0) == "hold"


def test_triage_pipeline_links_session_and_emits(tmp_path):
    st.add(tmp_path, "research ultrawide monitors\nemail the dentist\nbuy milk")
    data = st.load(tmp_path)
    data["settings"]["enabled_at"] = 0
    st.save(tmp_path, data)
    ids = {i["text"]: i["id"] for i in data["items"]}
    reply = json.dumps([
        {"id": ids["research ultrawide monitors"], "owner": "clover", "risk": "low", "plan": "compare 3"},
        {"id": ids["email the dentist"], "owner": "clover", "risk": "low", "external": False},
        {"id": ids["buy milk"], "owner": "you", "risk": "low"}])
    spawned, events = [], []
    started = tr.triage_pending(tmp_path, llm=lambda m: reply,
                                spawn=lambda it: spawned.append(it["text"]) or "sess-1",
                                emit=lambda e, p: events.append((e, p["reason"])), killed=False)
    assert started == [ids["research ultrawide monitors"]] and spawned == ["research ultrawide monitors"]
    by = {i["text"]: i for i in st.load(tmp_path)["items"]}
    assert by["research ultrawide monitors"]["session_id"] == "sess-1" and by["research ultrawide monitors"]["status"] == "working"
    assert by["email the dentist"]["status"] == "needs_you" and by["email the dentist"]["session_id"] is None
    assert by["buy milk"]["owner"] == "you" and by["buy milk"]["status"] == ""
    assert len(events) == 3 and all(e == ("todos.changed", "triage") for e in events)


def test_triage_llm_failure_never_starts(tmp_path):
    st.add(tmp_path, "research x")
    def boom(m): raise RuntimeError("down")
    assert tr.triage_pending(tmp_path, llm=boom, spawn=lambda i: "s", emit=lambda *a: None, killed=False) == []
    assert st.load(tmp_path)["items"][0]["triaged"] is True


def test_rate_limit_two_running(tmp_path):
    st.add(tmp_path, "research a\nresearch b\nresearch c")
    data = st.load(tmp_path); data["settings"]["enabled_at"] = 0; st.save(tmp_path, data)
    reply = json.dumps([{"id": i["id"], "owner": "clover", "risk": "low"} for i in data["items"]])
    n = []
    tr.triage_pending(tmp_path, llm=lambda m: reply, spawn=lambda it: n.append(1) or f"s{len(n)}", emit=lambda *a: None, killed=False)
    assert len(n) == 2


def test_finish_marks_done_or_needs_you(tmp_path):
    a, b = st.add(tmp_path, "research a\nresearch b")
    ev = []
    tr.finish_item(tmp_path, a["id"], "stuff\nSUMMARY: found 3 options", lambda e, p: ev.append(p["reason"]))
    tr.finish_item(tmp_path, b["id"], "SUMMARY: drafted\nNEEDS_APPROVAL: send to Sam", lambda e, p: ev.append(p["reason"]))
    by = {i["id"]: i for i in st.load(tmp_path)["items"]}
    assert by[a["id"]]["done"] and by[a["id"]]["done_by"] == "clover" and by[a["id"]]["result_summary"] == "found 3 options"
    assert not by[b["id"]]["done"] and by[b["id"]]["status"] == "needs_you"
    # owner can undo Clover's completion
    assert st.update(tmp_path, a["id"], {"done": False})["done"] is False


def test_rpc_handlers_broadcast(tmp_path, monkeypatch):
    from tui_gateway import server, todo_runtime

    monkeypatch.setattr(server, "get_clover_home", lambda: tmp_path)
    monkeypatch.setattr(todo_runtime, "kick", lambda home: None)
    sent = []
    monkeypatch.setattr(server, "_broadcast_global_event", lambda e, p=None: sent.append((e, p)))
    r = server._methods["todos.add"](1, {"text": "buy milk, call mom friday, plan trip"})
    items = r["result"]["items"]
    assert len(items) == 3 and sent[0][0] == "todos.changed"
    assert server._methods["todos.update"](2, {"id": items[0]["id"], "done": True})["result"]["item"]["done"]
    assert server._methods["todos.reorder"](3, {"ids": [items[2]["id"]]})["result"]["items"][0]["id"] == items[2]["id"]
    assert server._methods["todos.delete"](4, {"id": items[1]["id"]})["result"]["deleted"]
    assert "error" in server._methods["todos.delete"](5, {"id": "x"})
    assert "error" in server._methods["todos.add"](6, {"text": " "})
    assert len(server._methods["todos.list"](7, {})["result"]["items"]) == 2
    assert [e for e, _ in sent] == ["todos.changed"] * 4
    s = server._methods["todos.settings"](8, {"auto_start": False})["result"]["settings"]
    assert s["auto_start"] is False
