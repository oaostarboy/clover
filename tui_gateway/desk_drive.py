"""Phone desk control over the existing JSON-RPC channel (relay-safe).

``desk.take_control`` / ``desk.input`` / ``desk.release`` are thin wrappers over the SAME
``DesktopController`` the direct ``/desk/stream`` socket uses (same event schema, same
AUDIT log).  Paired phones need ``control_drive`` (1 h grant, enforced in
``device_auth.authorize_request``) AND ``desktop_control`` input enabled in config.
Owner transports keep working.  Refusals never close the socket.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

MAX_EVENTS = 60
MAX_CALLS_PER_SEC = 20
MAX_TEXT = 500
IDLE_RELEASE_SECONDS = 120.0
_SWEEP_INTERVAL = 2.0

_lock = threading.RLock()
_clients: Dict[str, object] = {}
_calls: Dict[str, List[float]] = {}
_last_seen: Dict[str, float] = {}
_display: Dict[str, int] = {}
_sweeper: Optional[threading.Thread] = None
_now = time.monotonic  # monkeypatched in tests


class DriveError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def reset_for_tests() -> None:
    with _lock:
        _clients.clear(); _calls.clear(); _last_seen.clear(); _display.clear()


def client_for(key: str) -> object:
    with _lock:
        return _clients.setdefault(key, object())


def _controller():
    from clover_cli import desktop_control as dc
    return dc.get_controller(dc.read_settings())


def release_device(key: str, why: str) -> bool:
    """Release control held by this device key (``dev:<id>`` or ``owner``)."""
    with _lock:
        client = _clients.get(key)
        _calls.pop(key, None)
        _display.pop(key, None)
        _last_seen.pop(key, None)
    from clover_cli import desktop_control as dc
    ctl = dc._controller
    if client is None or ctl is None:
        return False
    return ctl.release(client, why)


def _gate(disabled_reason: Optional[str] = None) -> Any:
    from clover_cli import desktop_control as dc
    s = dc.read_settings()
    if not s["enabled"] or not dc.input_allowed(s):
        raise DriveError(4030, "operation is not permitted for this device",
                         {"capability": "control_drive", "reason": "disabled"})
    return dc.get_controller(s)


def take_control(key: str, who: str, display: Any) -> Dict[str, Any]:
    ctl = _gate()
    try:
        n = int(display or 0)
    except (TypeError, ValueError):
        raise DriveError(4002, "display must be an integer")
    if not ctl.backend.display(n):
        raise DriveError(4002, "unknown display")
    client = client_for(key)
    ok, err = ctl.take(client, who)
    if not ok:
        if "another" in err:
            raise DriveError(4091, err)
        raise DriveError(5030, err)
    with _lock:
        _display[key] = n
        _last_seen[key] = _now()
    _start_sweeper()
    return {"owner": "user"}


def release(key: str) -> Dict[str, Any]:
    release_device(key, "client")
    return {"owner": None}


def _rate_ok(key: str) -> bool:
    now = _now()
    with _lock:
        hits = [t for t in _calls.get(key, []) if now - t < 1.0]
        if len(hits) >= MAX_CALLS_PER_SEC:
            _calls[key] = hits
            return False
        hits.append(now)
        _calls[key] = hits
        return True


def drive_input(key: str, events: Any) -> Dict[str, Any]:
    from clover_cli import desktop_control as dc
    ctl = _gate()
    if not _rate_ok(key):
        raise DriveError(4290, "rate_limited")
    if not isinstance(events, list) or not events:
        raise DriveError(4002, "events must be a non-empty list")
    if len(events) > MAX_EVENTS:
        raise DriveError(4002, f"at most {MAX_EVENTS} events per call")
    for e in events:
        if not isinstance(e, dict) or e.get("type") not in dc.INPUT_TYPES:
            raise DriveError(4002, "unknown input type")
        if e.get("type") == "type" and len(str(e.get("text", ""))) > MAX_TEXT:
            raise DriveError(4002, f"text is limited to {MAX_TEXT} characters")
    client = _clients.get(key)
    if client is None or ctl.owner is not client:
        raise DriveError(4040, "not controlling; call desk.take_control first")
    with _lock:
        default_display = _display.get(key, 0)
        _last_seen[key] = _now()
    accepted = 0
    for i, e in enumerate(events):
        m = {"display": default_display, **e}
        try:
            ctl.do_input(m)
        except Exception as exc:  # noqa: BLE001
            return {"accepted": accepted, "error": {"index": i, "message": f"input rejected: {type(exc).__name__}"}}
        ctl.inputs += 1
        accepted += 1
    return {"accepted": accepted}


def sweep() -> None:
    """Release control whose grant lapsed, device was revoked, or phone went quiet."""
    from clover_cli import desktop_control as dc
    ctl = dc._controller
    if ctl is None or ctl.owner is None:
        return
    with _lock:
        held = [(k, c) for k, c in _clients.items() if c is ctl.owner]
    for key, _ in held:
        if key.startswith("dev:"):
            try:
                from clover_cli.mobile_pairing import get_device_store
                row = get_device_store().get(key[4:])
                caps = set(row["effective_capabilities"]) if row else set()
            except Exception:
                caps = set()
            if "control_drive" not in caps:
                release_device(key, "revoked")
                continue
            if _now() - _last_seen.get(key, 0.0) > IDLE_RELEASE_SECONDS:
                release_device(key, "idle")


def _start_sweeper() -> None:
    global _sweeper
    with _lock:
        if _sweeper is not None and _sweeper.is_alive():
            return

        def run():
            while True:
                time.sleep(_SWEEP_INTERVAL)
                try:
                    sweep()
                except Exception:
                    pass

        _sweeper = threading.Thread(target=run, name="desk-drive-sweeper", daemon=True)
        _sweeper.start()


def caller():
    """(key, label): ``dev:<id>`` for a paired phone, ``owner`` otherwise."""
    from tui_gateway.device_auth import device_principal
    from tui_gateway.transport import current_transport

    principal = device_principal(current_transport())
    if principal is not None:
        return f"dev:{principal.device_id}", f"phone:{principal.device_id[:8]}"
    return "owner", "owner"
