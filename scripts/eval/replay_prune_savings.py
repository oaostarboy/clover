#!/usr/bin/env python3
"""Replay a deterministic tool-heavy session through the real ContextCompressor prune, priced with
prompt caching (cached prefix 0.1x, new tokens 1.25x, any history rewrite re-billed in full).

Prints billed input-token equivalents with the proactive prune OFF (old default) vs ON (new default,
cold-cache-only gating), for a session where the user pauses past the cache TTL every PAUSE_EVERY turns.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["CLOVER_HOME"] = tempfile.mkdtemp(prefix="replay-")

from agent.context_compressor import ContextCompressor, _estimate_msg_budget_tokens  # noqa: E402
from clover_state import SessionDB  # noqa: E402

TURNS, TOOLS_PER_TURN, PAUSE_EVERY = 40, 3, 4
SYSTEM_TOKENS = 10_000  # system prompt + tools[]


def _tokens(msgs):
    return SYSTEM_TOKENS + sum(_estimate_msg_budget_tokens(m) for m in msgs)


def replay(prune_on: bool) -> dict:
    db = SessionDB(db_path=Path(os.environ["CLOVER_HOME"]) / f"state-{prune_on}.db")
    sid = f"replay-{prune_on}"
    db.create_session(sid, source="cli")
    with patch("agent.context_compressor.get_model_context_length", return_value=1_000_000):
        comp = ContextCompressor(model="t", quiet_mode=True, proactive_prune_tokens=64_000 if prune_on else 0)
    comp._session_db, comp._session_id = db, sid
    msgs = [{"role": "system", "content": "s"}]
    cached, billed, commits = 0, 0.0, 0
    n = 0

    def call():
        nonlocal cached, billed
        total = _tokens(msgs)
        billed += min(cached, total) * 0.1 + max(0, total - cached) * 1.25
        cached = total

    for turn in range(TURNS):
        msgs.append({"role": "user", "content": f"task {turn}"})
        if prune_on and turn % PAUSE_EVERY == 0 and turn > 0:  # user came back after the cache expired
            prefix = msgs[:-1]
            db.append_messages_batch(sid, [m for m in prefix if not m.get("_db_persisted")]) if turn == PAUSE_EVERY else None
            pruned, count = comp.prune_tool_results_only(prefix, current_tokens=_tokens(prefix), cold_cache=True)
            if count and pruned is not prefix:
                msgs[:] = list(pruned) + msgs[-1:]
                commits += 1
        if turn % PAUSE_EVERY == 0:
            cached = 0  # cache expired during the pause
        for _ in range(TOOLS_PER_TURN):
            call()
            n += 1
            cid = f"c{turn}_{n}"
            msgs.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": cid, "type": "function", "function": {"name": "terminal", "arguments": '{"command":"cat big.log"}'}}]})
            body = "\n".join(f"row {i} " + "x" * 90 + str(n) for i in range(900))  # ~25K tokens
            msgs.append({"role": "tool", "tool_call_id": cid, "content": body})
        call()
        msgs.append({"role": "assistant", "content": "done"})
    return {"billed_input_token_equiv": int(billed), "prune_commits": commits, "final_context_tokens": _tokens(msgs)}


if __name__ == "__main__":
    off, on = replay(False), replay(True)
    print("prune OFF:", off)
    print("prune ON :", on)
    print(f"billed input change: {100 * (on['billed_input_token_equiv'] / off['billed_input_token_equiv'] - 1):+.1f}%")
