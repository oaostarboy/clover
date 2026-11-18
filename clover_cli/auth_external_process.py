"""Generic helpers for ``external_process`` model providers.

An external-process provider is an agent CLI launched as a local subprocess
instead of an HTTP endpoint. Its launch details live on the plugin's
``ProviderProfile`` (``process_command``, ``process_args``,
``process_command_env_vars``, ``process_args_env_var``), so core carries no
vendor-specific knowledge. copilot-acp predates this and keeps its own
hardcoded path in ``clover_cli.auth``.
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path
from typing import Any, Optional


def plugin_process_profile(provider_id: str) -> Optional[Any]:
    """The registered external_process ``ProviderProfile`` for *provider_id*, else None."""
    try:
        from providers import get_provider_profile

        profile = get_provider_profile(str(provider_id or "").strip().lower())
    except Exception:
        return None
    if profile is None or getattr(profile, "auth_type", "") != "external_process":
        return None
    return profile


def is_external_process_provider(provider_id: str) -> bool:
    """True when *provider_id* is an external_process provider (built-in or plugin)."""
    name = str(provider_id or "").strip().lower()
    if not name:
        return False
    try:
        from clover_cli.auth import PROVIDER_REGISTRY

        pconfig = PROVIDER_REGISTRY.get(name)
        if pconfig is not None:
            return pconfig.auth_type == "external_process"
    except Exception:
        pass
    return plugin_process_profile(name) is not None


def external_process_command(profile: Any) -> str:
    """First set ``process_command_env_vars`` override, else the profile's ``process_command``."""
    for var in getattr(profile, "process_command_env_vars", ()) or ():
        value = os.getenv(var, "").strip()
        if value:
            return value
    return str(getattr(profile, "process_command", "") or "")


def external_process_args(profile: Any) -> list[str]:
    """``process_args_env_var`` (shlex-split) when set, else the profile's ``process_args``."""
    var = getattr(profile, "process_args_env_var", "") or ""
    raw = os.getenv(var, "").strip() if var else ""
    if raw:
        return shlex.split(raw)
    return list(getattr(profile, "process_args", ()) or ())


def resolve_external_process_command(command: str) -> Optional[str]:
    """Absolute path of *command*: PATH first, then ``~/.local/bin`` (bare service PATH)."""
    if not command:
        return None
    if os.sep in command:
        path = os.path.abspath(os.path.expandvars(os.path.expanduser(command)))
        return path if os.path.isfile(path) and os.access(path, os.X_OK) else None
    found = shutil.which(command)
    if found:
        return found
    local = Path.home() / ".local" / "bin" / command
    return str(local) if local.is_file() and os.access(local, os.X_OK) else None
