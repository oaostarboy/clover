"""Tell the owner a paired phone is asking for rights.

Telegram (opt-in via ``mobile.request_alerts.telegram``) goes to the owner's
home channel through the same outbound lane cron delivery uses. The desktop
popup is raised by the desktop app, which polls the
owner endpoint (see ``/api/mobile/devices``). Messages never carry tokens.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict

_log = logging.getLogger(__name__)

_LABEL = {
    "chat": "chat",
    "history": "history",
    "media": "media",
    "model_selection": "model choice",
    "audio_transcribe": "dictation",
    "audio_speak": "speech",
    "crew_read": "Crew view",
    "crew_act": "Crew chat",
    "control_view": "Control view",
    "control_drive": "Control drive",
}


def request_text(device_name: str, capabilities: list, match_code: str) -> str:
    implied = {"crew_read" if "crew_act" in capabilities else None,
               "control_view" if "control_drive" in capabilities else None}
    rights = [_LABEL.get(c, c) for c in capabilities if c not in implied]
    what = ", ".join(rights) or "access"
    name = (device_name or "iPhone").strip()[:40] or "iPhone"
    return f"{name} wants {what}. Code {match_code}. Approve in Clover on your PC."


def _send_telegram(text: str) -> None:
    import asyncio

    from cron.scheduler import _get_home_target_chat_id, _get_home_target_thread_id
    from gateway.config import Platform, load_gateway_config
    from tools.send_message_tool import _send_to_platform

    chat_id = _get_home_target_chat_id("telegram")
    if not chat_id:
        _log.info("device request: no telegram home channel; popup only")
        return
    config = load_gateway_config()
    pconfig = config.platforms.get(Platform.TELEGRAM)
    if not pconfig or not pconfig.enabled:
        _log.info("device request: telegram not enabled; popup only")
        return
    if not getattr(pconfig, "token", None):
        # The isolated phone gateway does not load ~/.clover/.env into os.environ.
        from clover_cli.config import get_env_value

        pconfig.token = get_env_value("TELEGRAM_BOT_TOKEN")
    thread_id = _get_home_target_thread_id("telegram")
    result = asyncio.run(_send_to_platform(Platform.TELEGRAM, pconfig, chat_id, text, thread_id=thread_id))
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(str(result["error"])[:200])


def telegram_alerts_enabled() -> bool:
    """Telegram copies of access requests are opt-in: ``mobile.request_alerts.telegram``.

    The PC popup is where the owner approves (the code shows there), so a chat
    ping by default is noise.
    """
    try:
        from clover_cli.config import load_config_readonly

        mobile = (load_config_readonly() or {}).get("mobile") or {}
        alerts = mobile.get("request_alerts") or {}
        return alerts.get("telegram") is True
    except Exception:
        return False


def announce_request(device: Dict[str, Any], request: Dict[str, Any]) -> None:
    """Fire-and-forget Telegram ping when opted in; failure never blocks the RPC."""
    if not telegram_alerts_enabled():
        return
    text = request_text(device.get("name", "iPhone"), list(request.get("capabilities") or []),
                        str(request.get("match_code", "")))

    def run() -> None:
        try:
            _send_telegram(text)
        except Exception:
            _log.warning("device request telegram notice failed", exc_info=True)

    threading.Thread(target=run, name="device-request-notify", daemon=True).start()
