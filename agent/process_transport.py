"""Stall budgets for external-process providers (auth_type == "external_process").

Adapted from upstream 72a8b0e777 / a94b03c065 / b0714c2f1c. Such providers (e.g.
copilot-acp) are a child process behind a non-HTTP URL such as ``acp://copilot``,
so ``is_local_endpoint`` is False and the generic cloud stale timeout (90/150/240s)
would kill and re-send a healthy long buffered turn. Generic by auth_type so
plugin-defined external-process providers get the same treatment.
"""

from __future__ import annotations

import time
from typing import Any


def is_external_process_provider(provider: Any) -> bool:
    pid = str(provider or "").strip().lower()
    if not pid:
        return False
    try:
        from clover_cli.auth import PROVIDER_REGISTRY

        pconfig = PROVIDER_REGISTRY.get(pid)
    except Exception:
        return False
    return bool(pconfig and getattr(pconfig, "auth_type", None) == "external_process")


def implicit_non_stream_stale_timeout(agent: Any) -> float | None:
    """Buffered-call stall budget for an external process, else None (use cloud formula).

    With nothing configured the budget is the request timeout the client already
    gets (providers.<id>.request_timeout_seconds, else 1800s), capped by the run
    budget; it stays finite so a child that stopped reading stdin is still killed.
    An explicit stale timeout wins (returns None so the normal path honours it).
    """
    if not is_external_process_provider(getattr(agent, "provider", None)):
        return None
    if agent._stale_timeout_is_explicit():
        return None
    timeout = float(agent._resolved_api_call_timeout())
    run_budget = getattr(agent, "run_budget_seconds", None)
    started = getattr(agent, "_run_budget_started_at", None)
    if run_budget and started:
        remaining = float(run_budget) - (time.time() - started)
        timeout = min(timeout, max(60.0, remaining * 0.5))
    return timeout
