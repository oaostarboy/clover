"""``desk.take_control`` / ``desk.input`` / ``desk.release`` JSON-RPC handlers (see desk_drive.py).

Handlers are rebound onto server.py globals (method_ctx.py): helpers live in desk_drive.
"""

from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()
method = _registry.method


@method("desk.take_control")
def _(rid, params: dict) -> dict:
    """Params ``{display?}``. Result ``{owner: 'user'}``."""
    from tui_gateway import desk_drive as d

    try:
        key, who = d.caller()
        return _ok(rid, d.take_control(key, who, params.get("display", 0)))
    except d.DriveError as e:
        return _err(rid, e.code, e.message, e.data)
    except Exception:
        return _err(rid, 5031, "desk control failed")


@method("desk.input")
def _(rid, params: dict) -> dict:
    """Params ``{events: [...]}`` (<= 60, same schema as /desk/stream). Result ``{accepted, error?}``."""
    from tui_gateway import desk_drive as d

    try:
        key, _label = d.caller()
        return _ok(rid, d.drive_input(key, params.get("events")))
    except d.DriveError as e:
        return _err(rid, e.code, e.message, e.data)
    except Exception:
        return _err(rid, 5031, "desk control failed")


@method("desk.release")
def _(rid, params: dict) -> dict:
    from tui_gateway import desk_drive as d

    try:
        key, _label = d.caller()
        return _ok(rid, d.release(key))
    except Exception:
        return _err(rid, 5031, "desk control failed")


def register(server) -> None:
    _registry.install(server)
