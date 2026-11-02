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
import threading
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
LAST_SEEN_THROTTLE_SECONDS = 60
TOKEN_PREFIX = "cdt_"
MAX_NAME_LEN = 60


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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

    def _load(self) -> List[Dict[str, Any]]:
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            self._cache, self._mtime = [], None
            return self._cache
        if self._cache is not None and mtime == self._mtime:
            return self._cache
        try:
            data = json.loads(self.path.read_text("utf-8"))
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
        return d.get("last_seen_at") is None and now - float(d.get("created_at", 0)) > UNUSED_CODE_TTL_SECONDS

    def _active(self, d: Dict[str, Any], now: float) -> bool:
        return not d.get("revoked") and not self._expired(d, now)

    def create(self, name: str = "iPhone") -> Dict[str, Any]:
        """Create a device; returns the public record plus the one-time plaintext ``token``."""
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = self._clock()
        name = (name or "").strip()[:MAX_NAME_LEN] or "iPhone"
        rec = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "created_at": now,
            "last_seen_at": None,
            "token_hash": _hash(token),
            "revoked": False,
        }
        with self._lock:
            devices = list(self._load())
            # Drop dead entries (revoked / never-used expired) so the file stays small.
            devices = [d for d in devices if self._active(d, now)]
            devices.append(rec)
            self._save(devices)
        return {**self.public(rec, now), "token": token}

    def public(self, d: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        return {
            "id": d["id"],
            "name": d.get("name", "iPhone"),
            "created_at": d.get("created_at"),
            "last_seen_at": d.get("last_seen_at"),
            "connected": d.get("last_seen_at") is not None,
        }

    def list(self) -> List[Dict[str, Any]]:
        now = self._clock()
        with self._lock:
            return [self.public(d, now) for d in self._load() if self._active(d, now)]

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            devices = list(self._load())
            kept = [d for d in devices if d.get("id") != device_id]
            if len(kept) == len(devices):
                return False
            self._save(kept)
            return True

    def verify(self, token: str) -> bool:
        """True if ``token`` belongs to an active device; stamps last_seen (throttled)."""
        if not token or not token.startswith(TOKEN_PREFIX):
            return False
        digest = _hash(token)
        now = self._clock()
        with self._lock:
            devices = self._load()
            match = None
            for d in devices:
                # Compare every entry so timing does not reveal which one matched.
                if hmac.compare_digest(str(d.get("token_hash", "")), digest) and self._active(d, now):
                    match = d
            if match is None:
                return False
            last = match.get("last_seen_at")
            if last is None or now - float(last) >= LAST_SEEN_THROTTLE_SECONDS:
                match["last_seen_at"] = now
                try:
                    self._save(list(devices))
                except OSError:
                    pass
            return True


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


def create_pairing(config: Dict[str, Any], api_key: Optional[str], store: DeviceStore, name: str = "iPhone") -> Dict[str, Any]:
    """Mint a new per-phone token. The plaintext token is in this response only."""
    status = pairing_status(config, api_key)
    if not status["configured"]:
        return status
    dev = store.create(name)
    token = dev.pop("token")
    uri = f"clover://pair?origin={quote(status['origin'], safe='')}&token={quote(token, safe='')}"
    return {**status, "device": dev, "uri": uri, "token": token, "expires_in": UNUSED_CODE_TTL_SECONDS}
