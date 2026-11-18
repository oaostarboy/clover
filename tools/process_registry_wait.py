"""How long ``process action=wait`` may block for one call.

Shared by ``ProcessRegistry.wait`` (enforces the window) and the tool's
``deadline_floor`` declaration (lets the generic tool deadline clear it).
Adapted from upstream e1ac37eba1.
"""

import os


def max_wait_timeout() -> int:
    """``TERMINAL_TIMEOUT``: the default wait, and the cap on a requested one."""
    try:
        return int(os.getenv("TERMINAL_TIMEOUT", "180"))
    except (ValueError, TypeError):
        return 180


def process_deadline_floor(args: dict) -> float | None:
    if args.get("action") != "wait":
        return None
    max_timeout = float(max_wait_timeout())
    requested = args.get("timeout")
    if requested is None:
        return max_timeout
    try:
        requested = float(requested)
    except (TypeError, ValueError):
        return max_timeout
    return min(requested, max_timeout) if requested > 0 else None
