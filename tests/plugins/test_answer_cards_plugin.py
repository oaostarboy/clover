"""Answer cards: contract validation, producer, ACP ``_meta.clover.card``."""

import asyncio
import importlib.util
import json
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import acp
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "answer_cards_plugin", ROOT / "plugins" / "answer-cards" / "__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FORECAST = {
    "type": "forecast",
    "place": "Montreal",
    "now": {"temp": 3, "summary": "Cloudy", "icon": "cloud"},
    "days": [
        {"label": "Fri", "icon": "rain", "hi": 5, "lo": 1, "rain": 70},
        {"label": "Sat", "icon": "sun", "hi": 7, "lo": 0},
    ],
}
SCHEDULE = {
    "type": "schedule",
    "title": "Today",
    "events": [{"start": "09:00", "end": "09:30", "title": "Standup", "where": "Zoom"}],
}


@pytest.mark.parametrize("card", [FORECAST, SCHEDULE])
def test_valid_cards_become_v1_records(card):
    out = _load().build_card(card)
    assert out["v"] == 1 and out["type"] == card["type"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(type="chart"),
        lambda c: c.update(place=""),
        lambda c: c["now"].update(icon="snow"),
        lambda c: c.update(days=[]),
        lambda c: c.update(days=[c["days"][0]] * 8),
        lambda c: c.update(extra=1),
        lambda c: c["days"][0].update(hi="hot"),
    ],
)
def test_bad_forecast_rejected(mutate):
    card = json.loads(json.dumps(FORECAST))
    mutate(card)
    with pytest.raises(ValueError):
        _load().build_card(card)


def test_bad_schedule_rejected():
    bad = json.loads(json.dumps(SCHEDULE))
    bad["events"] = []
    with pytest.raises(ValueError):
        _load().build_card(bad)
    bad["events"] = [{"start": "9", "end": "10", "title": "x" * 500}]
    with pytest.raises(ValueError):
        _load().build_card(bad)


def test_handler_returns_card_or_error():
    mod = _load()
    ok = json.loads(mod.handler(FORECAST))
    assert ok["status"] == "success" and ok["clover_card"]["place"] == "Montreal"
    err = json.loads(mod.handler({"type": "forecast"}))
    assert err["status"] == "error" and "clover_card" not in err


def test_register_is_one_small_tool():
    mod = _load()
    tools = []
    ctx = MagicMock()
    ctx.register_tool = lambda **kw: tools.append(kw)
    mod.register(ctx)
    assert [t["name"] for t in tools] == ["answer_card"]
    assert len(json.dumps(tools[0]["schema"])) < 2500  # cheap on tokens


def test_acp_complete_update_carries_card_meta():
    from acp_adapter.events import make_step_cb

    conn = MagicMock(spec=acp.Client)
    conn.session_update = AsyncMock()
    loop = asyncio.new_event_loop()
    import threading

    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        ids = {"answer_card": deque(["tc-1"])}
        cb = make_step_cb(conn, "s1", loop, ids, {})
        card = _load().build_card(FORECAST)
        cb(1, [{"name": "answer_card", "result": json.dumps({"status": "success", "clover_card": card})}])
        update = conn.session_update.call_args[0][1]
        dumped = update.model_dump(by_alias=True, exclude_none=True)
        assert dumped["_meta"]["clover"]["card"] == card
        assert dumped["kind"] == "other"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(2)
        loop.close()


def test_acp_no_card_meta_without_valid_card():
    from acp_adapter.events import card_meta_from_result

    assert card_meta_from_result("answer_card", json.dumps({"status": "error"})) is None
    assert card_meta_from_result("terminal", json.dumps({"clover_card": {"v": 1}})) is None
    assert card_meta_from_result("answer_card", "not json") is None
    assert card_meta_from_result("answer_card", json.dumps({"clover_card": {"v": 2, "type": "forecast"}})) is None
