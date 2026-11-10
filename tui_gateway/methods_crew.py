"""Crew JSON-RPC handlers: ``crew.list`` and ``peer.turn`` (see crew.py).

Handlers are rebound onto server.py globals (method_ctx.py): keep helpers in
``tui_gateway.crew`` and import lazily inside the handler.
"""

from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()
method = _registry.method


def _owner_ok():
    """True for the owner's authenticated WS session or the legacy owner token.

    Rejects: no bound transport, and the server-internal credential.
    """
    from tui_gateway.methods_browser_control import _INTERNAL_PROVIDER, _INTERNAL_USER_ID
    from tui_gateway.transport import current_transport

    transport = current_transport()
    if transport is None:
        return False
    identity = getattr(transport, "auth_identity", None)
    if identity is None:
        return True
    if not isinstance(identity, dict):
        return False
    if identity.get("provider") == "mobile-device" or identity.get("device_principal") is not None:
        # A paired phone is never the owner. crew_act is enforced in device_auth;
        # this is the second lock should a rule ever be widened by mistake.
        principal = identity.get("device_principal")
        return "crew_act" in getattr(principal, "capabilities", ())
    uid, prov = identity.get("user_id"), identity.get("provider")
    if not (isinstance(uid, str) and uid.strip() and isinstance(prov, str) and prov.strip()):
        return False
    return not (uid == _INTERNAL_USER_ID and prov == _INTERNAL_PROVIDER)


def crew_roster() -> list:
    """Merged crew rows (local profiles + bot_peers). Raises on failure. Shared by ``crew.list`` and the to-do assignee check."""
    from clover_cli.profiles import list_profiles
    from tui_gateway import crew

    rows = []
    for p in list_profiles():
        display = (getattr(p, "display_name", "") or "").strip()
        if not display:
            try:
                from clover_cli.config import read_user_config_raw

                cfg = read_user_config_raw(p.path / "config.yaml")
                cfg = cfg if isinstance(cfg, dict) else {}
                b = cfg.get("branding") if isinstance(cfg.get("branding"), dict) else {}
                a = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
                n = b.get("agent_name") or a.get("name") or ""
                display = n.strip() if isinstance(n, str) else ""
            except Exception:
                display = ""
        rows.append({"name": p.name, "is_default": bool(p.is_default), "display_name": display})
    from clover_cli.subcommands.peer import _load_peers

    return crew.build_crew(rows, _load_peers())


@method("crew.list")
def _(rid, params: dict) -> dict:
    """One merged crew: local profiles + configured bot_peers.

    Result ``{agents: [{id, display_name, kind, runtime, host_label, online,
    last_seen, avatar_initial}]}``. Never includes URLs or keys.
    """
    # Handlers are rebound onto server.py's globals, so module helpers are imported here.
    from tui_gateway.methods_crew import crew_roster

    try:
        return _ok(rid, {"agents": crew_roster()})
    except Exception as e:
        return _err(rid, 5120, str(e))


@method("peer.turn")
def _(rid, params: dict) -> dict:
    """Relay one turn to a peer agent. Params: ``peer``, ``text``,
    ``room_id?``, ``session_hint?``. Result ``{peer, session_id, reply}``;
    pass ``session_id`` back as ``session_hint`` to keep context.
    Owner-session only; errors (incl. timeouts) are RPC errors.
    """
    from tui_gateway import crew
    from tui_gateway.methods_crew import _owner_ok

    if not _owner_ok():
        return _err(rid, 4403, "owner session required")
    try:
        out = crew.relay_turn(
            str(params.get("peer") or ""),
            str(params.get("text") or ""),
            room_id=(str(params["room_id"]) if params.get("room_id") else None),
            session_hint=(str(params["session_hint"]) if params.get("session_hint") else None),
        )
        return _ok(rid, out)
    except crew.PeerRelayError as e:
        return _err(rid, e.code, e.message)
    except Exception:
        return _err(rid, 5121, "peer relay failed")


@method("device.status")
def _(rid, params: dict) -> dict:
    """What this paired phone may do right now. Result ``{status, capabilities,
    expires: {cap: iso}, pending_request?: {request_id, match_code, capabilities}}``."""
    from clover_cli.mobile_pairing import get_device_store

    from tui_gateway.device_auth import device_principal
    from tui_gateway.transport import current_transport

    principal = device_principal(current_transport())
    if principal is None:
        return _err(rid, 4031, "device.status is for paired phones")
    row = get_device_store().get(principal.device_id)
    if row is None:
        return _err(rid, 4030, "device authorization expired or revoked")
    out = {"status": row["status"], "capabilities": row["effective_capabilities"], "expires": row["expires"]}
    if "pending_request" in row:
        out["pending_request"] = {k: row["pending_request"][k] for k in ("request_id", "match_code", "capabilities")}
    return _ok(rid, out)


@method("device.request_access")
def _(rid, params: dict) -> dict:
    """Ask the owner for rights. Params ``{capabilities: [...]}``. Result ``{request_id, match_code}``;
    the PC shows a popup and Telegram pings the owner. Rate limited to one per 10 s."""
    from clover_cli.mobile_pairing import get_device_store, normalize_capabilities
    from clover_cli.mobile_device_notify import announce_request

    from tui_gateway.device_auth import device_principal
    from tui_gateway.transport import current_transport

    principal = device_principal(current_transport())
    if principal is None:
        return _err(rid, 4031, "device.request_access is for paired phones")
    try:
        caps = normalize_capabilities(params.get("capabilities") or [])
    except ValueError as exc:
        return _err(rid, 4002, str(exc))
    if not caps:
        return _err(rid, 4002, "capabilities required")
    store = get_device_store()
    try:
        req = store.request_access(principal.device_id, caps)
    except ValueError as exc:
        return _err(rid, 4290 if str(exc) == "rate_limited" else 4002, str(exc))
    if req is None:
        return _err(rid, 4030, "device authorization expired or revoked")
    if req["created"]:
        store.audit(principal.device_id, ",".join(caps), "device.request_access", "request")
        announce_request(store.get(principal.device_id) or {}, req)
    return _ok(rid, {"request_id": req["request_id"], "match_code": req["match_code"]})


def register(server) -> None:
    _registry.install(server)
