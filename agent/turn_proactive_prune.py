"""Proactive tool-result prune call sites, with the safety gates that keep prompt caching intact.

A committed prune rewrites bodies the provider has already cached, so it only runs when that
costs nothing and cannot hurt the conversation in flight:

* the provider cache has already expired (``provider_cache_expired``) -- never while it is warm;
* never the running turn: only history before this turn's user message is touched;
* never the session's first turn (nothing older exists, and the first request builds the prefix);
* only with a bound session row that keeps the originals (archive pointers name them).

Nothing here imports ``agent.conversation_loop`` at module level (cycle).
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from agent.prompt_caching import provider_cache_expired

logger = logging.getLogger("agent.conversation_loop")


def _accepts_cold_cache(prune: Any) -> bool:
    try:
        params = inspect.signature(prune).parameters
    except (TypeError, ValueError):
        return False
    return "cold_cache" in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


def prune_stale_prefix(
    agent: Any, messages: list[dict[str, Any]], current_turn_user_idx: Any, current_tokens: int | None,
) -> list[dict[str, Any]]:
    """Prune tool results older than the running turn when the provider cache is already cold.

    Returns a new list (same length, same row positions, only bodies replaced) on a commit, else
    the INPUT list object.
    """
    idx = current_turn_user_idx
    if (not getattr(agent, "compression_enabled", False) or not isinstance(idx, int)
            or not 0 < idx < len(messages)
            or int(getattr(agent, "_user_turn_count", 0) or 0) < 2):  # first turn: nothing to prune
        return messages
    compressor = getattr(agent, "context_compressor", None)
    prune = getattr(compressor, "prune_tool_results_only", None)
    if (not callable(prune) or getattr(compressor, "_session_db", None) is None
            or not getattr(compressor, "_session_id", "") or not _accepts_cold_cache(prune)):
        return messages
    prefix = messages[:idx]
    if not provider_cache_expired(agent, prefix):
        return messages  # warm cache: a rewrite would be a paid cache break
    try:
        pruned, count = prune(prefix, current_tokens=current_tokens, cold_cache=True)
    except Exception:
        logger.debug("proactive tool-result prune failed; skipping", exc_info=True)
        return messages
    if not count or pruned is prefix or len(pruned) != len(prefix):
        return messages
    return list(pruned) + messages[idx:]
