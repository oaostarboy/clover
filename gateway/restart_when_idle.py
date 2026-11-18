"""Background work that must finish before a graceful gateway restart.

A requested restart waits for the gateway to be idle: no running turn, no live
background subagent, no queued subagent completion notice (each of those
delivers a new turn later, so restarting now would drop or repeat it). The wait
is bounded by ``agent.restart_after_turn_timeout`` in config.yaml.

Adapted from NousResearch/hermes-agent 579565e0e1 (gateway/restart_when_idle.py).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

_LIVE_STATES = ("running", "stalling")


def delegation_units(now: float | None = None) -> List[Dict[str, Any]]:
    from tools.async_delegation import list_async_delegations

    now = time.time() if now is None else now
    units = []
    for rec in list_async_delegations():
        if rec.get("status") not in _LIVE_STATES:
            continue
        unit = {"kind": "delegation", "delegation_id": rec.get("delegation_id")}
        if rec.get("dispatched_at"):
            unit["elapsed_s"] = round(now - float(rec["dispatched_at"]), 1)
        units.append(unit)
    return units


def queued_notice_units(runner: Any) -> List[Dict[str, Any]]:
    """Subagent completions queued (or taken but not yet delivered)."""
    from tools.process_registry import process_registry

    queue = process_registry.completion_queue
    with queue.mutex:
        events = list(queue.queue)
    events += list(getattr(runner, "_async_notices_in_delivery", ()) or ())
    return [
        {"kind": "notice", "id": e.get("delegation_id")}
        for e in events
        if isinstance(e, dict) and e.get("type") == "async_delegation"
        and not e.get("_no_transport")
    ]


def background_units(runner: Any) -> List[Dict[str, Any]]:
    return delegation_units() + queued_notice_units(runner)


def describe(units: List[Dict[str, Any]]) -> str:
    return "; ".join(
        f"{u.get('kind')}({u.get('delegation_id') or u.get('id') or ''})" for u in units
    ) or "nothing"
