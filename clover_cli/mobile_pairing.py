"""Phone pairing info for the desktop "Connect your iPhone" section.

The iPhone app scans ``clover://pair?origin=<https origin>&token=<bearer>``.
It rejects anything but a plain https origin, so this module never returns an
http/LAN address: with no public https URL configured the state is simply
``configured: False``.

Each phone gets its own device token (see ``DeviceStore``): random, stored
only as a sha256 hash, revocable, and expiring if never used. The master
``API_SERVER_KEY`` never appears in a QR.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse


def strict_https_origin(value: Any) -> Optional[str]:
    """Return ``https://host[:port]`` or None. Mirrors the iOS strictHttpsOrigin."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or not host:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    if parsed.path not in ("", "/"):
        return None
    host = f"[{host}]" if ":" in host else host
    return f"https://{host}" + (f":{port}" if port and port != 443 else "")


UNUSED_CODE_TTL_SECONDS = 10 * 60
ACTIVE_TOKEN_TTL_SECONDS = 90 * 24 * 60 * 60
LAST_SEEN_THROTTLE_SECONDS = 60
TOKEN_PREFIX = "cdt_"
MAX_NAME_LEN = 60


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


DEVICE_CAPABILITIES = frozenset({"chat", "history", "media", "model_selection"})
AUDIO_CAPABILITIES = frozenset({"audio_transcribe", "audio_speak"})
# Rights that need an explicit owner grant on the PC. Never part of a legacy record.
CREW_CAPABILITIES = frozenset({"crew_read", "crew_act"})
CONTROL_CAPABILITIES = frozenset({"control_view", "control_drive"})
GRANTED_CAPABILITIES = CREW_CAPABILITIES | CONTROL_CAPABILITIES
BASE_CAPABILITIES = DEVICE_CAPABILITIES | AUDIO_CAPABILITIES
KNOWN_DEVICE_CAPABILITIES = BASE_CAPABILITIES | GRANTED_CAPABILITIES
# A grant implies another: control_drive -> control_view, crew_act -> crew_read.
IMPLIED_CAPABILITIES = {"control_drive": "control_view", "crew_act": "crew_read"}
# Capabilities that lapse on their own (seconds from grant). control_view lasts until revoked.
GRANT_TTL_SECONDS = {"control_drive": 60 * 60}
REQUEST_TTL_SECONDS = 10 * 60
REQUEST_MIN_INTERVAL_SECONDS = 10
AUDIT_FILE = "mobile-device-audit.log"


def normalize_capabilities(caps: Any) -> List[str]:
    """Validate a requested capability list and add implied rights. Raises ValueError."""
    if not isinstance(caps, (list, tuple, set, frozenset)) or any(not isinstance(c, str) for c in caps):
        raise ValueError("capabilities must be a list of strings")
    wanted = set(caps)
    unknown = wanted - KNOWN_DEVICE_CAPABILITIES
    if unknown:
        raise ValueError("unknown capability")
    for cap, needed in IMPLIED_CAPABILITIES.items():
        if cap in wanted:
            wanted.add(needed)
    return sorted(wanted)


def _stored_capabilities(record: Dict[str, Any]) -> Optional[frozenset[str]]:
    # Profile-bound records predating explicit scopes retain only their old grants.
    if "capabilities" not in record:
        return DEVICE_CAPABILITIES
    caps = record["capabilities"]
    if (not isinstance(caps, list) or any(not isinstance(c, str) for c in caps)
            or len(set(caps)) != len(caps) or not set(caps) <= KNOWN_DEVICE_CAPABILITIES):
        return None
    return frozenset(caps)


def _grant_expiries(record: Dict[str, Any]) -> Dict[str, float]:
    raw = record.get("grant_expires_at")
    if not isinstance(raw, dict):
        return {}
    return {k: float(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, (int, float))}


def _record_status(record: Dict[str, Any]) -> str:
    # Records without ``status`` predate approvals: approved with exactly their stored caps.
    return "pending" if record.get("status") == "pending" else "approved"


def _record_lapsed(record: Dict[str, Any], now: float) -> frozenset[str]:
    stored = _stored_capabilities(record) or frozenset()
    return frozenset(c for c, t in _grant_expiries(record).items() if c in stored and t <= now)


def _record_capabilities(record: Dict[str, Any], now: Optional[float] = None) -> Optional[frozenset[str]]:
    """Effective rights: stored caps minus lapsed grants; nothing at all while pending."""
    caps = _stored_capabilities(record)
    if caps is None:
        return None
    if _record_status(record) == "pending":
        return frozenset()
    return caps - _record_lapsed(record, time.time() if now is None else now)


def denial_reason(principal: "DevicePrincipal", capability: str) -> Dict[str, str]:
    """Stable refusal body for a valid device that lacks ``capability``."""
    if principal.status == "pending":
        reason = "pending"
    elif capability in principal.lapsed:
        reason = "expired"
    else:
        reason = "not_granted"
    return {"capability": capability, "reason": reason}


_AUDIO_PATHS = ("/api/audio/transcribe", "/api/audio/speak")
DESK_GET_PATHS = ("/desk/displays", "/desk/snapshot")


def device_http_gate(request, principal: "DevicePrincipal") -> Any:
    """Decide one HTTP request from a device credential.

    Returns ``None`` when allowed, ``"unauthorized"`` when the route is simply not
    part of the device surface (401, as before), or a JSON-able dict (HTTP 403) when
    the route is a device route the device has not been granted.
    """
    import re

    method, path = request.method, request.url.path
    if method == "POST" and path in _AUDIO_PATHS:
        if request.scope.get("raw_path", b"").split(b"?", 1)[0] != path.encode("ascii"):
            return "unauthorized"
        query = request.query_params.multi_items()
        if len(query) > 1 or (query and query[0] != ("profile", principal.profile)):
            return "unauthorized"
        cap = "audio_transcribe" if path.endswith("transcribe") else "audio_speak"
        return None if cap in principal.capabilities else {"error": "forbidden", **denial_reason(principal, cap)}
    if method == "POST" and path == "/api/auth/ws-ticket":
        return None
    if method != "GET":
        return "unauthorized"
    if path in DESK_GET_PATHS:
        if request.scope.get("raw_path", b"").split(b"?", 1)[0] != path.encode("ascii"):
            return "unauthorized"
        # q and w are admitted by the relay for forward compatibility; the gateway ignores them (stills use desktop_control.width).
        allowed_query = {"display", "q", "w"} if path.endswith("snapshot") else set()
        if set(request.query_params) - allowed_query:
            return "unauthorized"
        display = request.query_params.get("display")
        if display is not None and not re.fullmatch(r"[0-9]{1,3}", display):
            return "unauthorized"
        if any(not re.fullmatch(r"[0-9]{1,4}", request.query_params.get(k, "0")) for k in ("q", "w")):
            return "unauthorized"
        if "control_view" not in principal.capabilities:
            return {"error": "forbidden", **denial_reason(principal, "control_view")}
        try:
            from clover_cli.desktop_control import read_settings
            enabled = read_settings()["enabled"]
        except Exception:
            enabled = False
        if not enabled:
            return {"error": "forbidden", "capability": "control_view", "reason": "disabled"}
        return None
    if path in ("/api/files/download", "/api/artifacts/thumb"):
        if request.scope.get("raw_path", b"").split(b"?", 1)[0] != path.encode("ascii"):
            return "unauthorized"
        keys = set(request.query_params)
        if path.endswith("download"):
            if keys != {"path"}:
                return "unauthorized"
        elif not ({"id"} <= keys <= {"id", "size"}):
            return "unauthorized"
        if "media" not in principal.capabilities:
            return {"error": "forbidden", **denial_reason(principal, "media")}
        if not _creation_listed(path, request):
            return {"error": "forbidden", "capability": "media", "reason": "not_listed"}
        return None
    if path == "/api/sessions":
        ok_query = not (set(request.query_params) - {"limit", "offset", "min_messages", "archived", "order"})
    elif re.fullmatch(r"/api/sessions/[^/]+/messages", path):
        ok_query = not request.query_params
    else:
        return "unauthorized"
    if not ok_query:
        return "unauthorized"
    if "history" not in principal.capabilities:
        return {"error": "forbidden", **denial_reason(principal, "history")}
    return None


def _creation_listed(path: str, request) -> bool:
    """Only files the Creations index listed (artifacts.list wrote them to its id registry)."""
    try:
        from clover_constants import get_clover_home
        from tui_gateway import artifact_index

        home = get_clover_home()
        if path.endswith("thumb"):
            return artifact_index.lookup(home, request.query_params.get("id", "")) is not None
        return artifact_index.lookup(home, artifact_index.artifact_id(request.query_params.get("path", ""))) is not None
    except Exception:
        return False


def device_request_allowed(request, principal: "DevicePrincipal") -> bool:
    """True when the request is within the device's current grants."""
    return device_http_gate(request, principal) is None


@dataclass(frozen=True)
class DevicePrincipal:
    device_id: str
    profile: str
    expires_at: float
    capabilities: frozenset[str]
    status: str = "approved"
    lapsed: frozenset[str] = frozenset()


class DeviceStore:
    """Per-phone device tokens. Only sha256 hashes are persisted (0600, atomic)."""

    def __init__(self, path: Optional[Path] = None, clock=time.time):
        if path is None:
            from clover_constants import get_clover_home

            path = Path(get_clover_home()) / "mobile_devices.json"
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._mtime: Optional[float] = None

    @contextmanager
    def _transaction(self):
        # SQLite's write lock serializes the complete read/modify/atomic-replace
        # transaction across processes, not merely across DeviceStore instances.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock.sqlite3")
        with self._lock:
            connection = sqlite3.connect(str(lock_path), timeout=30)
            try:
                os.chmod(lock_path, 0o600)
                connection.execute("BEGIN IMMEDIATE")
                self._cache = None
                yield
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _load(self) -> List[Dict[str, Any]]:
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            self._cache, self._mtime = [], None
            return self._cache
        if self._cache is not None and mtime == self._mtime:
            return self._cache
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            devices = [d for d in data.get("devices", []) if isinstance(d, dict)]
        except (OSError, ValueError, AttributeError):
            devices = []
        self._cache, self._mtime = devices, mtime
        return devices

    def _save(self, devices: List[Dict[str, Any]]) -> None:
        from utils import atomic_json_write

        atomic_json_write(self.path, {"version": 1, "devices": devices}, mode=0o600)
        self._cache = devices
        self._mtime = self.path.stat().st_mtime_ns

    def _expired(self, d: Dict[str, Any], now: float) -> bool:
        return (now - float(d.get("created_at", 0)) >= ACTIVE_TOKEN_TTL_SECONDS
                or (d.get("last_seen_at") is None and now - float(d.get("created_at", 0)) > UNUSED_CODE_TTL_SECONDS))

    def _active(self, d: Dict[str, Any], now: float) -> bool:
        return not d.get("revoked") and not self._expired(d, now)

    def create(self, name: str = "iPhone", *, pending: bool = False) -> Dict[str, Any]:
        """Create a device; returns the public record plus the one-time plaintext ``token``.

        ``pending=True`` (relay pairing) starts with no rights until the owner approves
        on the PC. Other pairing paths keep minting the full base set as before.
        """
        from clover_cli.profiles import get_active_profile_name

        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = self._clock()
        name = (name or "").strip()[:MAX_NAME_LEN] or "iPhone"
        rec = {
            "profile": get_active_profile_name(),
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "created_at": now,
            "last_seen_at": None,
            "token_hash": _hash(token),
            "revoked": False,
            "capabilities": [] if pending else sorted(BASE_CAPABILITIES),
        }
        if pending:
            rec["status"] = "pending"
        with self._transaction():
            devices = list(self._load())
            # Drop dead entries (revoked / never-used expired) so the file stays small.
            devices = [d for d in devices if self._active(d, now)]
            devices.append(rec)
            self._save(devices)
        return {**self.public(rec, now), "token": token}

    def public(self, d: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        now = self._clock() if now is None else now
        out = {
            "id": d["id"],
            "name": d.get("name", "iPhone"),
            "created_at": d.get("created_at"),
            "last_seen_at": d.get("last_seen_at"),
            "connected": d.get("last_seen_at") is not None,
            "status": _record_status(d),
        }
        stored = _stored_capabilities(d) or frozenset()
        out["capabilities"] = sorted(stored)
        out["effective_capabilities"] = sorted(_record_capabilities(d, now) or frozenset())
        out["expires"] = {c: _iso(t) for c, t in sorted(_grant_expiries(d).items()) if c in stored}
        req = self._live_request(d, now)
        if req:
            out["pending_request"] = {k: req[k] for k in ("request_id", "match_code", "capabilities", "created_at")}
        return out

    @staticmethod
    def _live_request(d: Dict[str, Any], now: float) -> Optional[Dict[str, Any]]:
        req = d.get("pending_request")
        if not isinstance(req, dict) or float(req.get("created_at", 0)) + REQUEST_TTL_SECONDS <= now:
            return None
        return req

    def list(self) -> List[Dict[str, Any]]:
        now = self._clock()
        with self._transaction():
            return [self.public(d, now) for d in self._load() if self._active(d, now)]

    def relay_devices(self, profile: str) -> List[Dict[str, Any]]:
        """Routing grants only; never disclose bearer credentials or owner keys."""
        now = self._clock()
        with self._transaction():
            result = []
            for device in self._load():
                if device.get("profile") != profile or not self._active(device, now):
                    continue
                digest = device.get("token_hash", "")
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    continue
                expires = float(device["created_at"]) + ACTIVE_TOKEN_TTL_SECONDS
                if device.get("last_seen_at") is None:
                    expires = min(expires, float(device["created_at"]) + UNUSED_CODE_TTL_SECONDS)
                result.append({"hash": digest, "expiresAt": int(expires * 1000)})
            return result

    def revoke(self, device_id: str) -> bool:
        with self._transaction():
            devices = list(self._load())
            kept = [d for d in devices if d.get("id") != device_id]
            if len(kept) == len(devices):
                return False
            self._save(kept)
            return True

    def verify(self, token: str) -> bool:
        return self.authenticate(token) is not None

    def authenticate(self, token: str) -> Optional[DevicePrincipal]:
        """Resolve a credential to a scoped principal without retaining the secret."""
        if not token or not token.startswith(TOKEN_PREFIX):
            return None
        digest = _hash(token)
        now = self._clock()
        with self._transaction():
            devices = self._load()
            match = None
            for d in devices:
                if hmac.compare_digest(str(d.get("token_hash", "")), digest) and self._active(d, now):
                    match = d
            # Legacy unscoped tokens cannot safely acquire inferred authority.
            if match is None or not isinstance(match.get("profile"), str):
                return None
            caps = _record_capabilities(match, now)
            if caps is None:
                return None
            last = match.get("last_seen_at")
            if last is None or now - float(last) >= LAST_SEEN_THROTTLE_SECONDS:
                match["last_seen_at"] = now
                self._save(list(devices))
            return self._principal(match, caps, now)

    @staticmethod
    def _principal(d: Dict[str, Any], caps: frozenset, now: float) -> DevicePrincipal:
        return DevicePrincipal(d["id"], d["profile"], float(d["created_at"]) + ACTIVE_TOKEN_TTL_SECONDS,
                               caps, _record_status(d), _record_lapsed(d, now))

    def principal_is_active(self, principal: DevicePrincipal) -> bool:
        """Is this device still paired (not revoked/expired)? Grants are NOT compared:
        a changed grant must refuse calls (4030), never drop the connection."""
        return self.current_principal(principal) is not None

    def current_principal(self, principal: DevicePrincipal) -> Optional[DevicePrincipal]:
        """Re-read persisted state; the returned principal carries the rights in force now."""
        if not isinstance(principal, DevicePrincipal):
            return None
        now = self._clock()
        with self._transaction():
            for d in self._load():
                if (d.get("id") == principal.device_id
                        and d.get("profile") == principal.profile
                        and float(d.get("created_at", 0)) + ACTIVE_TOKEN_TTL_SECONDS == principal.expires_at
                        and self._active(d, now)):
                    caps = _record_capabilities(d, now)
                    return None if caps is None else self._principal(d, caps, now)
        return None

    # -- owner decisions ---------------------------------------------------------
    def _mutate(self, device_id: str, fn) -> Optional[Dict[str, Any]]:
        now = self._clock()
        with self._transaction():
            devices = list(self._load())
            for d in devices:
                if d.get("id") == device_id and self._active(d, now):
                    result = fn(d, now)
                    self._save(devices)
                    return result
        return None

    def get(self, device_id: str) -> Optional[Dict[str, Any]]:
        now = self._clock()
        with self._transaction():
            for d in self._load():
                if d.get("id") == device_id and self._active(d, now):
                    return self.public(d, now)
        return None

    def _apply_caps(self, d: Dict[str, Any], caps: List[str], now: float) -> None:
        d["capabilities"] = caps
        old = _grant_expiries(d)
        expiries = {}
        for cap in caps:
            ttl = GRANT_TTL_SECONDS.get(cap)
            if ttl is None:
                continue
            # A still-running grant keeps its clock; a new or lapsed one starts now.
            expiries[cap] = old[cap] if cap in old and old[cap] > now else now + ttl
        d["grant_expires_at"] = expiries

    def approve(self, device_id: str, capabilities: Any, *, request_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Owner approval: ADD the approved rights to what an approved device already has
        (lapsed grants are not carried over), clear the request. Rights only leave through
        ``set_capabilities`` / revoke. ``None`` if unknown."""
        wanted = normalize_capabilities(capabilities)

        def apply(d, now):
            req = d.get("pending_request")
            if request_id is not None and not (isinstance(req, dict) and req.get("request_id") == request_id):
                raise ValueError("request not found")
            caps = wanted
            if _record_status(d) == "approved":
                have = _record_capabilities(d, now) or frozenset()
                caps = normalize_capabilities(set(wanted) | set(have))
            self._apply_caps(d, caps, now)
            d["status"] = "approved"
            d["approved_at"] = now
            d["approved_by"] = "desktop"
            d.pop("pending_request", None)
            return self.public(d, now)

        out = self._mutate(device_id, apply)
        if out is not None:
            self.audit(device_id, "*", "owner.approve", "allow", detail=",".join(wanted))
        return out

    def set_capabilities(self, device_id: str, capabilities: Any) -> Optional[Dict[str, Any]]:
        caps = normalize_capabilities(capabilities)

        def apply(d, now):
            if _record_status(d) == "pending":
                raise ValueError("device is pending; approve it first")
            self._apply_caps(d, caps, now)
            return self.public(d, now)

        out = self._mutate(device_id, apply)
        if out is not None:
            self.audit(device_id, "*", "owner.set_capabilities", "allow", detail=",".join(caps))
        return out

    def deny(self, device_id: str, request_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Deny a request. A still-pending (never approved) device is removed; an approved
        device just loses the request and keeps its current rights."""
        def apply(d, now):
            req = d.get("pending_request")
            if request_id is not None and not (isinstance(req, dict) and req.get("request_id") == request_id):
                raise ValueError("request not found")
            d.pop("pending_request", None)
            return _record_status(d)

        status = self._mutate(device_id, apply)
        if status is None:
            return None
        self.audit(device_id, "*", "owner.deny", "deny")
        if status == "pending":
            self.revoke(device_id)
            return {"id": device_id, "removed": True}
        return self.get(device_id)

    def request_access(self, device_id: str, capabilities: Any) -> Optional[Dict[str, Any]]:
        """Create (or return the live) access request. Returns
        ``{request_id, match_code, capabilities, created}``; ``None`` for an unknown device."""
        caps = normalize_capabilities(capabilities) if capabilities else sorted(BASE_CAPABILITIES)

        def apply(d, now):
            req = self._live_request(d, now)
            if req and sorted(req["capabilities"]) == caps:
                return {**{k: req[k] for k in ("request_id", "match_code", "capabilities")}, "created": False}
            if now - float(d.get("last_request_at", 0)) < REQUEST_MIN_INTERVAL_SECONDS:
                raise ValueError("rate_limited")
            req = {"request_id": uuid.uuid4().hex[:16], "match_code": f"{secrets.randbelow(10000):04d}",
                   "capabilities": caps, "created_at": now}
            d["pending_request"] = req
            d["last_request_at"] = now
            return {**{k: req[k] for k in ("request_id", "match_code", "capabilities")}, "created": True}

        return self._mutate(device_id, apply)

    # -- audit ---------------------------------------------------------------------
    def _audit_path(self) -> Path:
        return self.path.parent / "logs" / AUDIT_FILE

    def audit(self, device_id: str, capability: str, method: str, decision: str, *, detail: str = "") -> None:
        """Append one event. Never tokens, never prompt text."""
        line = {"t": _iso(self._clock()), "device": device_id, "cap": capability,
                "method": method[:64], "decision": decision}
        if detail:
            line["detail"] = detail[:200]
        try:
            path = self._audit_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
                path.replace(path.with_suffix(".log.1"))
            fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (json.dumps(line, separators=(",", ":")) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        except OSError:
            pass

    def audit_tail(self, device_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            lines = self._audit_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: List[Dict[str, Any]] = []
        for raw in reversed(lines):
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("device") == device_id:
                out.append(ev)
                if len(out) >= limit:
                    break
        return out[::-1]

    def owner_list(self) -> List[Dict[str, Any]]:
        """Device rows for the desktop owner UI (adds the audit tail)."""
        return [{**row, "audit": self.audit_tail(row["id"], 20)} for row in self.list()]


_default_store: Optional[DeviceStore] = None


def get_device_store() -> DeviceStore:
    global _default_store
    from clover_constants import get_clover_home

    path = Path(get_clover_home()) / "mobile_devices.json"
    if _default_store is None or _default_store.path != path:
        _default_store = DeviceStore(path)
    return _default_store


def _cors_https_origins(value: Any) -> List[str]:
    if isinstance(value, str):
        value = value.split(",")
    return [v for v in (value if isinstance(value, (list, tuple)) else []) if isinstance(v, str)]


def resolve_origin(config: Dict[str, Any]) -> Optional[str]:
    """Phone-reachable https origin, never http/LAN. Order:

    a. ``mobile.public_url``
    b. an already-configured public URL for this gateway: the API server's
       ``platforms.api_server.extra.public_url``, then the dashboard's
       ``CLOVER_DASHBOARD_PUBLIC_URL`` / ``dashboard.public_url``, then the first
       https, non-loopback entry of the API server's CORS origins (what a
       Cloudflare tunnel hostname is typically allow-listed as)
    c. None (the desktop shows an inline field)
    """
    cfg = config if isinstance(config, dict) else {}
    mobile = cfg.get("mobile")
    found = strict_https_origin(mobile.get("public_url") if isinstance(mobile, dict) else None)
    if found:
        return found
    platforms = cfg.get("platforms")
    api = platforms.get("api_server") if isinstance(platforms, dict) else None
    extra = api.get("extra") if isinstance(api, dict) else None
    extra = extra if isinstance(extra, dict) else {}
    dashboard = cfg.get("dashboard")
    candidates = [
        extra.get("public_url"),
        os.environ.get("CLOVER_DASHBOARD_PUBLIC_URL"),
        dashboard.get("public_url") if isinstance(dashboard, dict) else None,
    ]
    candidates += _cors_https_origins(extra.get("cors_origins", os.environ.get("API_SERVER_CORS_ORIGINS", "")))
    for c in candidates:
        origin = strict_https_origin(c)
        if origin and urlparse(origin).hostname not in ("localhost", "127.0.0.1", "::1"):
            return origin
    return None


def pairing_status(config: Dict[str, Any], api_key: Optional[str]) -> Dict[str, Any]:
    """Setup state only. Never returns a credential."""
    origin = resolve_origin(config)
    if not origin:
        return {"configured": False, "reason": "no_public_https_url", "origin": None}
    if len((api_key or "").strip()) < 6:
        return {"configured": False, "reason": "no_gateway_key", "origin": origin}
    return {"configured": True, "reason": None, "origin": origin}


def create_relay_pairing(store: DeviceStore) -> Dict[str, Any]:
    """Owner-only desktop mint; Electron registers its hash before exposing QR."""
    origin = "https://relay.cloverai.ca"
    dev = store.create("iPhone")
    token = dev.pop("token")
    uri = f"clover://pair?origin={quote(origin, safe='')}&token={quote(token, safe='')}"
    return {"configured": True, "reason": None, "origin": origin, "managed": True,
            "device": dev, "uri": uri, "token": token, "expires_in": UNUSED_CODE_TTL_SECONDS}


def create_pairing(config: Dict[str, Any], api_key: Optional[str], store: DeviceStore, name: str = "iPhone") -> Dict[str, Any]:
    """Mint a new per-phone token. The plaintext token is in this response only."""
    status = pairing_status(config, api_key)
    if not status["configured"]:
        return status
    dev = store.create(name)
    token = dev.pop("token")
    uri = f"clover://pair?origin={quote(status['origin'], safe='')}&token={quote(token, safe='')}"
    return {**status, "device": dev, "uri": uri, "token": token, "expires_in": UNUSED_CODE_TTL_SECONDS}
