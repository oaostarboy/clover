"""APNs sender + device-token store for iOS reply notifications.

Service-gated: with ``push.apns.enabled`` false, or no key file, every public
entry point is a silent no-op. Direct HTTP/2 to APNs with ES256 token auth.
Secrets (the .p8) live at ``push.apns.key_path``; nothing secret is logged.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

HOST_PRODUCTION = "https://api.push.apple.com"
HOST_SANDBOX = "https://api.sandbox.push.apple.com"
_JWT_TTL_S = 50 * 60
_BODY_MAX = 180
_DEAD_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}

_DEFAULTS = {
    "enabled": False,
    "key_id": "",
    "team_id": "",
    "key_path": "~/.clover/secrets/apns/AuthKey.p8",
    "bundle_id": "ca.silasstudios.clover",
}


def load_config(cfg: Optional[dict] = None) -> dict:
    if cfg is None:
        try:
            from clover_cli.config import load_config as _load

            cfg = _load()
        except Exception:
            cfg = {}
    raw = ((cfg or {}).get("push") or {}).get("apns") or {}
    out = dict(_DEFAULTS)
    out.update({k: v for k, v in raw.items() if k in _DEFAULTS})
    return out


# ── payload helpers ────────────────────────────────────────────────────────
_MD_SUBS = [
    (re.compile(r"```[^\n]*\n?(.*?)```", re.S), r"\1"),
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),
    (re.compile(r"^\s*>\s?", re.M), ""),
    (re.compile(r"^\s*[-*+]\s+", re.M), ""),
    (re.compile(r"(\*\*|__)(.+?)\1", re.S), r"\2"),
    (re.compile(r"(?<![\w*])[*_]([^*_\n]+)[*_](?![\w*])"), r"\1"),
]


def strip_markdown(text: str) -> str:
    for pat, rep in _MD_SUBS:
        text = pat.sub(rep, text or "")
    return re.sub(r"\s+", " ", text).strip()


def build_payload(title: str, text: str, session_id: str, *, error: bool = False) -> dict:
    body = strip_markdown(text)
    if len(body) > _BODY_MAX:
        body = body[: _BODY_MAX - 1].rstrip() + "…"
    return {
        "aps": {
            "alert": {"title": (title or "Clover").strip() or "Clover", "body": body},
            "sound": "default",
            "thread-id": session_id,
        },
        "session_id": session_id,
    }


# ── JWT ────────────────────────────────────────────────────────────────────
class _JWTCache:
    def __init__(self) -> None:
        self._tok: Optional[str] = None
        self._at = 0.0
        self._lock = threading.Lock()

    def get(self, key_pem: bytes, key_id: str, team_id: str, now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        with self._lock:
            if self._tok and now - self._at < _JWT_TTL_S:
                return self._tok
            self._tok = build_jwt(key_pem, key_id, team_id, now)
            self._at = now
            return self._tok


def build_jwt(key_pem: bytes, key_id: str, team_id: str, now: Optional[float] = None) -> str:
    import jwt  # PyJWT[crypto] is a core dependency

    iat = int(time.time() if now is None else now)
    return jwt.encode({"iss": team_id, "iat": iat}, key_pem, algorithm="ES256",
                      headers={"kid": key_id})


# ── device token store ─────────────────────────────────────────────────────
class TokenStore:
    """Small JSON file (mode 600) under CLOVER_HOME, deduped by token."""

    def __init__(self, path: Optional[Path] = None) -> None:
        if path is None:
            from clover_constants import get_clover_home

            path = Path(get_clover_home()) / "push" / "apns_devices.json"
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [d for d in data if isinstance(d, dict) and d.get("token")]
        except Exception:
            return []

    def _write(self, rows: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def register(self, token: str, env: str = "production", device_name: str = "") -> None:
        env = "development" if env in ("development", "sandbox") else "production"
        with self._lock:
            rows = [r for r in self._read() if r["token"] != token]
            rows.append({"token": token, "env": env, "device_name": device_name or "",
                         "updated_at": int(time.time())})
            self._write(rows)

    def unregister(self, token: str) -> bool:
        with self._lock:
            rows = self._read()
            kept = [r for r in rows if r["token"] != token]
            if len(kept) != len(rows):
                self._write(kept)
            return len(kept) != len(rows)

    def all(self) -> list[dict]:
        with self._lock:
            return self._read()


# ── sender ─────────────────────────────────────────────────────────────────
Transport = Callable[[str, dict, bytes], "tuple[int, dict]"]


class ApnsSender:
    def __init__(self, cfg: Optional[dict] = None, store: Optional[TokenStore] = None,
                 transport: Optional[Transport] = None) -> None:
        self.cfg = load_config(cfg)
        self.store = store or TokenStore()
        self._transport = transport
        self._jwt = _JWTCache()
        self._client = None

    def _key(self) -> Optional[bytes]:
        p = Path(os.path.expanduser(str(self.cfg["key_path"])))
        try:
            return p.read_bytes() if p.is_file() else None
        except OSError:
            return None

    def available(self) -> bool:
        c = self.cfg
        return bool(c["enabled"] and c["key_id"] and c["team_id"] and self._key())

    def _default_transport(self, url: str, headers: dict, body: bytes):
        if self._client is None:
            try:
                import h2  # noqa: F401
            except ImportError:
                from tools.lazy_deps import ensure

                ensure("push.apns", prompt=False)
            import httpx

            self._client = httpx.Client(http2=True, timeout=10.0)
        r = self._client.post(url, headers=headers, content=body)
        try:
            data = r.json() if r.content else {}
        except Exception:
            data = {}
        return r.status_code, data

    def send(self, payload: dict, *, collapse_id: Optional[str] = None) -> int:
        """Send to every registered device. Returns count delivered (202)."""
        if not self.available():
            return 0
        key = self._key()
        try:
            jwt_tok = self._jwt.get(key, self.cfg["key_id"], self.cfg["team_id"])
        except Exception as exc:
            logger.warning("apns: JWT build failed: %s", type(exc).__name__)
            return 0
        body = json.dumps(payload, ensure_ascii=False).encode()
        headers = {
            "authorization": f"bearer {jwt_tok}",
            "apns-topic": self.cfg["bundle_id"],
            "apns-push-type": "alert",
            "apns-priority": "10",
        }
        if collapse_id:
            headers["apns-collapse-id"] = collapse_id[:64]
        send = self._transport or self._default_transport
        ok = 0
        for dev in self.store.all():
            host = HOST_SANDBOX if dev.get("env") == "development" else HOST_PRODUCTION
            url = f"{host}/3/device/{dev['token']}"
            try:
                status, data = send(url, headers, body)
            except Exception as exc:
                logger.warning("apns: send failed (%s)", type(exc).__name__)
                continue
            reason = (data or {}).get("reason", "")
            if status == 200 or status == 202:
                ok += 1
            elif status == 410 or reason in _DEAD_REASONS:
                self.store.unregister(dev["token"])
                logger.info("apns: removed dead device token (%s)", reason or status)
            else:
                logger.warning("apns: rejected status=%s reason=%s", status, reason)
        logger.info("apns: delivered=%d devices=%d", ok, len(self.store.all()) + 0)
        return ok


# ── trigger ────────────────────────────────────────────────────────────────
_sender: Optional[ApnsSender] = None
_app_background = False


def get_sender() -> ApnsSender:
    global _sender
    if _sender is None:
        _sender = ApnsSender()
    return _sender


def set_background(flag: bool) -> None:
    global _app_background
    _app_background = bool(flag)


def is_background() -> bool:
    return _app_background


def should_push(*, detached: bool, background: bool, status: str) -> bool:
    if status in ("interrupted", "cancelled"):
        return False
    return bool(detached or background)


def notify_turn_complete(sid: str, title: str, text: str, status: str, error: str = "",
                         *, detached: bool, sender: Optional[ApnsSender] = None) -> bool:
    """Fire-and-forget push for a *final* reply. Never raises."""
    try:
        s = sender or get_sender()
        if not s.available() or not should_push(detached=detached, background=_app_background, status=status):
            return False
        if status == "error":
            reason = strip_markdown(error or text or "")
            payload = build_payload("Clover hit an error", reason or "The turn failed.", sid, error=True)
        else:
            if not strip_markdown(text):
                return False
            payload = build_payload(title or "Clover", text, sid)
        threading.Thread(target=s.send, args=(payload,), kwargs={"collapse_id": sid},
                         daemon=True, name="apns-send").start()
        return True
    except Exception:
        logger.debug("apns trigger failed", exc_info=True)
        return False
