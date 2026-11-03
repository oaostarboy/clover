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
    uid, prov = identity.get("user_id"), identity.get("provider")
    if not (isinstance(uid, str) and uid.strip() and isinstance(prov, str) and prov.strip()):
        return False
    return not (uid == _INTERNAL_USER_ID and prov == _INTERNAL_PROVIDER)


@method("crew.list")
def _(rid, params: dict) -> dict:
    """One merged crew: local profiles + configured bot_peers.

    Result ``{agents: [{id, display_name, kind, runtime, host_label, online,
    last_seen, avatar_initial}]}``. Never includes URLs or keys.
    """
    try:
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

        return _ok(rid, {"agents": crew.build_crew(rows, _load_peers())})
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


def register(server) -> None:
    _registry.install(server)
