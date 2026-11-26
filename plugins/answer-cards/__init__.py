"""answer-cards: one small opt-in tool that renders a forecast or schedule card.

Wire shape (``clover.structured-tool/1`` card payloads, see
``integrations/generative-ui/contract.ts``)::

    {"v": 1, "type": "forecast", "place", "now": {temp, summary, icon}, "days": [...]}
    {"v": 1, "type": "schedule", "title", "events": [{start, end, title, where?}]}

ACP clients receive it as ``_meta.clover.card`` on the tool call update
(``acp_adapter.events``). Read-only: no actions, links or HTML.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict

NAME = "answer_card"
VERSION = 1
ICONS = ("sun", "cloud", "rain")

SCHEMA = {
    "type": "object",
    "required": ["type"],
    "properties": {
        "type": {"type": "string", "enum": ["forecast", "schedule"]},
        "place": {"type": "string", "description": "forecast: city"},
        "now": {
            "type": "object",
            "description": "forecast: {temp (number), summary, icon sun|cloud|rain}",
        },
        "days": {
            "type": "array",
            "maxItems": 7,
            "description": "forecast: [{label 'Fri', icon, hi, lo, rain? percent}]",
        },
        "title": {"type": "string", "description": "schedule: heading"},
        "events": {
            "type": "array",
            "maxItems": 12,
            "description": "schedule: [{start 'HH:MM', end, title, where?}]",
        },
    },
}

DESCRIPTION = (
    "Show a weather forecast or a day schedule as a card in the chat. Use only when "
    "the user asked for that data and a card is clearer than text; still answer in a "
    "sentence. Never for anything else."
)


def _text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError("text bounds")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("control character")
    value.encode("utf-8")
    return value


def _num(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("number")
    return value


def _closed(value: Any, required: set, optional: set = frozenset()) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("object")
    keys = set(value)
    if not required <= keys or not keys <= required | optional:
        raise ValueError("closed schema")
    return value


def _icon(value: Any) -> str:
    if value not in ICONS:
        raise ValueError("icon")
    return value


def build_card(args: Dict[str, Any]) -> Dict[str, Any]:
    """Validate tool args and return the closed v1 card record. Raises ValueError."""
    kind = args.get("type") if isinstance(args, dict) else None
    if kind == "forecast":
        a = _closed(args, {"type", "place", "now", "days"})
        now = _closed(a["now"], {"temp", "summary", "icon"})
        days = a["days"]
        if not isinstance(days, list) or not 1 <= len(days) <= 7:
            raise ValueError("day bounds")
        out_days = []
        for d in days:
            d = _closed(d, {"label", "icon", "hi", "lo"}, {"rain"})
            day = {"label": _text(d["label"], 16), "icon": _icon(d["icon"]),
                   "hi": _num(d["hi"]), "lo": _num(d["lo"])}
            if "rain" in d:
                day["rain"] = _num(d["rain"])
            out_days.append(day)
        return {"v": VERSION, "type": "forecast", "place": _text(a["place"], 120),
                "now": {"temp": _num(now["temp"]), "summary": _text(now["summary"], 120),
                        "icon": _icon(now["icon"])},
                "days": out_days}
    if kind == "schedule":
        a = _closed(args, {"type", "title", "events"})
        events = a["events"]
        if not isinstance(events, list) or not 1 <= len(events) <= 12:
            raise ValueError("event bounds")
        out = []
        for e in events:
            e = _closed(e, {"start", "end", "title"}, {"where"})
            ev = {"start": _text(e["start"], 8), "end": _text(e["end"], 8),
                  "title": _text(e["title"], 120)}
            if "where" in e:
                ev["where"] = _text(e["where"], 120)
            out.append(ev)
        return {"v": VERSION, "type": "schedule", "title": _text(a["title"], 120), "events": out}
    raise ValueError("unknown card type")


def handler(args, **_kwargs):
    try:
        card = build_card(args)
    except (ValueError, KeyError, TypeError) as exc:
        return json.dumps({"status": "error", "error": f"invalid card: {exc}"})
    return json.dumps({"status": "success", "clover_card": card})


def register(ctx):
    ctx.register_tool(
        name=NAME,
        toolset="answer-cards",
        description=DESCRIPTION,
        schema={"name": NAME, "description": DESCRIPTION, "parameters": SCHEMA},
        handler=handler,
        check_fn=lambda: True,
    )
