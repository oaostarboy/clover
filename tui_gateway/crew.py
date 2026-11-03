"""Crew roster + peer relay plumbing for the phone's Crew tab.

Pure helpers (no server globals) so they are importable and unit-testable.
The JSON-RPC wrappers live in ``methods_crew.py``.

* ``build_crew``   — ONE merged list of local profiles + ``bot_peers``.
* ``relay_turn``   — one outbound turn to a peer through the SAME transport
  ``clover peer dm`` uses (``clover_cli.subcommands.peer``), imported not
  shelled out. Keys are read there and never returned or logged.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT_S = 2.0
HEALTH_TTL_S = 30.0
PEER_TURN_TIMEOUT_S = 600

# Canonical display names for known fleet peers (config keys are lowercase
# and some are abbreviations, e.g. ``vega`` -> Vegas).
_PEER_DISPLAY = {
    "octavia": "Octavia",
    "alfred": "Alfred",
    "suni": "Suni",
    "oracle": "Oracle",
    "vega": "Vegas",
    "vegas": "Vegas",
}
_PEER_RUNTIME = {
    "octavia": "Hermes",
    "alfred": "Clover C1",
    "suni": "Clover C1",
    "oracle": "Clover C1",
    "vega": "OpenClaw",
    "vegas": "OpenClaw",
}

_health_cache: dict[str, tuple[float, bool]] = {}
_health_lock = threading.Lock()


def _peer_display_name(key: str, entry: dict) -> str:
    explicit = str(entry.get("display_name") or entry.get("name") or "").strip()
    if explicit:
        return explicit
    return _PEER_DISPLAY.get(key.lower(), key[:1].upper() + key[1:])


def _host_label(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        host = ""
    return host


def probe_health(url: str, *, timeout: float = HEALTH_TIMEOUT_S) -> bool:
    """Cheap ``GET <url>/health`` — True on any 2xx."""
    try:
        req = urllib.request.Request(
            url.rstrip("/") + "/health", headers={"User-Agent": "clover-crew-probe"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — registered peer URL
            return 200 <= int(resp.status) < 300
    except Exception:
        return False


def check_online(
    urls: dict[str, str],
    *,
    probe: Callable[[str], bool] | None = None,
    now: Callable[[], float] = time.monotonic,
    ttl: float = HEALTH_TTL_S,
) -> dict[str, tuple[bool, float | None]]:
    """Concurrent, 30s-cached health for ``{id: url}`` → ``{id: (online, checked_wall)}``."""
    probe = probe or probe_health
    out: dict[str, tuple[bool, float | None]] = {}
    stale: dict[str, str] = {}
    t = now()
    with _health_lock:
        for ident, url in urls.items():
            hit = _health_cache.get(url)
            if hit and t - hit[0] < ttl:
                out[ident] = (hit[1], None)
            else:
                stale[ident] = url
    if stale:
        with ThreadPoolExecutor(max_workers=min(8, len(stale))) as pool:
            results = list(pool.map(lambda kv: (kv[0], bool(probe(kv[1]))), stale.items()))
        t2 = now()
        with _health_lock:
            for ident, ok in results:
                _health_cache[stale[ident]] = (t2, ok)
                out[ident] = (ok, time.time())
    return out


def clear_health_cache() -> None:
    with _health_lock:
        _health_cache.clear()


def build_crew(
    profiles: list[dict],
    peers: dict,
    *,
    probe: Callable[[str], bool] | None = None,
    profile_urls: dict[str, str] | None = None,
    now: Callable[[], float] = time.monotonic,
) -> list[dict]:
    """Merge local profile rows and ``bot_peers`` into one crew list.

    ``profiles``: rows shaped like ``profiles.list`` output (``name``,
    ``display_name``, ``is_default``, ``last_session``). Local profiles are
    online by definition (this gateway answered). A peer whose id matches a
    profile name (octavia) is dropped — the local profile path wins.
    """
    crew: list[dict] = []
    seen: set[str] = set()
    for p in profiles:
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        is_default = bool(p.get("is_default")) or name == "default"
        display = str(p.get("display_name") or "").strip() or ("Clover" if is_default else name[:1].upper() + name[1:])
        last = p.get("last_session") if isinstance(p.get("last_session"), dict) else None
        crew.append(
            {
                "id": name,
                "display_name": display,
                "kind": "profile",
                "runtime": "Clover C1",
                "host_label": "This gateway",
                "online": True,
                "last_seen": (last or {}).get("last_active") or None,
                "avatar_initial": display[:1].upper(),
            }
        )
        seen.add(name.lower())
        seen.add(display.lower())

    peer_rows = []
    for key, entry in sorted((peers or {}).items()):
        if not isinstance(entry, dict) or not entry.get("url"):
            continue
        display = _peer_display_name(str(key), entry)
        if str(key).lower() in seen or display.lower() in seen:
            continue  # same agent as a local profile
        peer_rows.append((str(key), display, entry))
    health = check_online({k: str(e["url"]) for k, _, e in peer_rows}, probe=probe, now=now)
    for key, display, entry in peer_rows:
        online, checked = health.get(key, (False, None))
        crew.append(
            {
                "id": key,
                "display_name": display,
                "kind": "peer",
                "runtime": str(entry.get("runtime") or _PEER_RUNTIME.get(key.lower(), "Clover")),
                "host_label": str(entry.get("host_label") or _host_label(str(entry["url"])) or key),
                "online": online,
                "last_seen": checked if online else None,
                "avatar_initial": display[:1].upper(),
            }
        )
    return crew


class PeerRelayError(Exception):
    """User-presentable relay failure. Never contains keys."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def room_session_title(room_id: str) -> str:
    return f"Group: {room_id}"


def relay_turn(
    peer: str,
    text: str,
    *,
    room_id: str | None = None,
    session_hint: str | None = None,
    transport: Any = None,
    timeout: int = PEER_TURN_TIMEOUT_S,
) -> dict:
    """One outbound turn to ``peer``; returns ``{peer, session_id, reply}``.

    ``transport`` defaults to ``clover_cli.subcommands.peer`` (the module
    ``peer dm`` runs on). Session routing: ``session_hint`` (a session id the
    peer gave us earlier) is reused; otherwise a per-room session titled
    ``Group: <room_id>`` is found/created on the peer; with no room, the
    peer's Bot Chat. This is always a FRESH outbound turn — never call it
    from inside an inbound peer turn (deadlock rule).
    """
    t = transport
    if t is None:
        from clover_cli.subcommands import peer as t  # same code as `clover peer dm`

    text = (text or "").strip()
    if not text:
        raise PeerRelayError(4100, "text required")
    try:
        name, profile = t._parse_target(peer)
    except ValueError as exc:
        raise PeerRelayError(4101, str(exc)) from None
    entry = t._load_peers().get(name)
    if not isinstance(entry, dict) or not entry.get("url"):
        raise PeerRelayError(4102, f"no peer named '{name}'")
    key = t._peer_secret(name)
    if not key:
        raise PeerRelayError(4103, f"no API key configured for peer '{name}'")
    base = t._base_url(entry, profile)
    try:
        session_id = (session_hint or "").strip()
        if not session_id and room_id:
            session_id = _ensure_room_session(t, base, key, room_session_title(room_id))
        if not session_id:
            session_id = t._ensure_bot_chat(base, key)
        result = t._request(
            f"{base}/api/sessions/{urllib.parse.quote(session_id, safe='')}/chat",
            key,
            method="POST",
            body={"message": text},
            timeout=timeout,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and session_hint:
            raise PeerRelayError(4104, f"peer '{name}' no longer has that session") from None
        raise PeerRelayError(4105, f"peer '{name}' rejected the request (HTTP {exc.code})") from None
    except TimeoutError:
        raise PeerRelayError(4106, f"peer '{name}' didn't respond in time") from None
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise PeerRelayError(4106, f"peer '{name}' didn't respond in time") from None
        raise PeerRelayError(4107, f"could not reach peer '{name}'") from None
    except RuntimeError as exc:
        raise PeerRelayError(4108, f"peer '{name}': {str(exc)[:200]}") from None
    except OSError:
        raise PeerRelayError(4107, f"could not reach peer '{name}'") from None
    msg = result.get("message")
    reply = str(msg.get("content") or "") if isinstance(msg, dict) else ""
    logger.info("peer.turn peer=%s room=%s session=%s reply_chars=%d", name, room_id or "-", session_id, len(reply))
    return {"peer": name, "session_id": str(result.get("session_id") or session_id), "reply": reply}


def _ensure_room_session(t: Any, base: str, key: str, title: str) -> str:
    """Find-or-create the peer-side session for a group room (hidden lookup first)."""
    query = urllib.parse.urlencode({"limit": 200, "title": title, "include_hidden": 1})
    listing = t._request(f"{base}/api/sessions?{query}", key)
    for s in listing.get("data") or []:
        if isinstance(s, dict) and (s.get("title") or "").strip() == title and s.get("id"):
            return str(s["id"])
    created = t._request(f"{base}/api/sessions", key, method="POST", body={"title": title, "source": "bot_peer_dm"})
    s = created.get("session") if isinstance(created.get("session"), dict) else created
    sid = str(s.get("id") or s.get("session_id") or "")
    if not sid:
        raise RuntimeError("peer did not return a session id")
    return sid
