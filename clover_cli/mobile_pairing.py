"""Phone pairing info for the desktop "Connect your iPhone" section.

The iPhone app scans ``clover://pair?origin=<https origin>&token=<bearer>``.
It rejects anything but a plain https origin, so this module never returns an
http/LAN address: with no public https URL configured the state is simply
``configured: False``.

Token: the gateway has a single shared bearer (``API_SERVER_KEY``), so the
phone reuses it. It is returned only to the authenticated desktop caller and
is never logged.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse


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


def pairing_info(config: Dict[str, Any], api_key: Optional[str]) -> Dict[str, Any]:
    """Pairing state from config (``mobile.public_url``) and the gateway key."""
    mobile = config.get("mobile") if isinstance(config, dict) else None
    raw = mobile.get("public_url") if isinstance(mobile, dict) else None
    origin = strict_https_origin(raw)
    token = (api_key or "").strip()
    if not origin:
        return {"configured": False, "reason": "no_public_https_url", "origin": None, "token": None}
    if len(token) < 6:
        return {"configured": False, "reason": "no_gateway_key", "origin": origin, "token": None}
    return {"configured": True, "reason": None, "origin": origin, "token": token, "shared_token": True}
