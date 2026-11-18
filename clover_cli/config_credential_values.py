"""Refuse to persist redacted credential masks (2.1).

The agent only sees credentials through the redactor and display maskers
(``sk-pro...WXYZ``, ``***``, ``[REDACTED]``). A writer that stores one of
those as the credential turns a working key into a dead string (401s).
This is hardening at the write edge, not a security boundary.

Adapted from upstream hermes-agent 05348a30 ``config_credential_values.py``
(simplified: no scoped-env or stored-value password comparison).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterator

_ELLIPSIS_MASK_RE = re.compile(r"\S{0,8}(?:\.\.\.|…)(?:\S{4})?")
_STARS_RE = re.compile(r"\*{3,}\S{0,4}")
_BARE_MASKS = frozenset({"[redacted]", "<redacted>", "(redacted)", "redacted"})
_FENCED_REDACTED_RE = re.compile(r"(?i)([*#=_-])\1+\s*redacted\s*\1+")
_ENV_REF_RE = re.compile(r"\$\{[^}]+\}")
_AUTH_SCHEME_RE = re.compile(r"(?i)(?:bearer|basic|token|digest)\s+")
_KEPT_PREFIX_RE = re.compile(r"\d+:|[A-Za-z][A-Za-z0-9]{0,7}[-_]")
_PASSWORD_NAME_RE = re.compile(r"(?:^|_)(?:password|passwd|passphrase)$")
_CRED_ENV_NAME_RE = re.compile(r"(?:^|_)(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD|ACCESS_KEY)$")


def _is_marker_word(candidate: str) -> bool:
    lowered = candidate.lower()
    return (
        lowered in _BARE_MASKS
        or lowered.startswith(("«redacted", "[redacted"))
        or bool(_FENCED_REDACTED_RE.fullmatch(candidate))
    )


def _is_bare_mask(candidate: str, *, shapes: bool = True) -> bool:
    if _is_marker_word(candidate):
        return True
    return shapes and bool(
        _STARS_RE.fullmatch(candidate) or _ELLIPSIS_MASK_RE.fullmatch(candidate)
    )


def _mask_candidate(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip().strip("\"'")
    scheme = _AUTH_SCHEME_RE.match(candidate)
    if scheme:
        candidate = candidate[scheme.end():]
    return "" if _ENV_REF_RE.fullmatch(candidate) else candidate


def _is_masked_token(candidate: str, shapes: bool) -> bool:
    if _is_bare_mask(candidate, shapes=shapes):
        return True
    prefix = _KEPT_PREFIX_RE.match(candidate)
    return bool(prefix) and _is_bare_mask(candidate[prefix.end():], shapes=shapes)


def is_redacted_credential_value(value: Any, *, shapes: bool = True) -> bool:
    """True when ``value`` is a redaction/display mask, not a usable secret."""
    candidate = _mask_candidate(value)
    if not candidate:
        return False
    if _is_marker_word(candidate):
        return True
    return all(_is_masked_token(t, shapes) for t in candidate.split())


def _is_password_name(key: str) -> bool:
    return bool(_PASSWORD_NAME_RE.search(key.rsplit(".", 1)[-1].lower().replace("-", "_")))


def _is_mask(key: str, value: Any) -> bool:
    # Human-chosen passwords may legitimately look like "****": only refuse
    # explicit marker words for those.
    return is_redacted_credential_value(value, shapes=not _is_password_name(key))


def _is_credential_env_name(key: str) -> bool:
    from clover_cli.config_defaults import OPTIONAL_ENV_VARS

    meta = OPTIONAL_ENV_VARS.get(key.upper())
    return bool(_CRED_ENV_NAME_RE.search(key.upper())) or (
        isinstance(meta, dict) and meta.get("password") is True
    )


def refuse_redacted_env_value(key: str, value: Any) -> None:
    """Raise ``ValueError`` when a credential ``.env`` entry would store a mask."""
    if _is_credential_env_name(key) and _is_mask(key, value):
        raise ValueError(
            f"Refusing to save {key.upper()}: the value is a redacted display mask, "
            "not the credential. Enter the real secret (or leave it unchanged)."
        )


def _credential_leaves(doc: Any, path: tuple = ()) -> Iterator[tuple]:
    from clover_cli.config import _SECRET_CONFIG_KEYS

    if isinstance(doc, dict):
        items = [(str(k), v) for k, v in doc.items()]
    elif isinstance(doc, list):
        items = [(str(i), v) for i, v in enumerate(doc)]
    else:
        return
    for key, child in items:
        if key.lower() in _SECRET_CONFIG_KEYS and not isinstance(child, (dict, list)):
            yield ".".join(path + (key,)), child
        else:
            yield from _credential_leaves(child, path + (key,))


def refuse_redacted_config_credentials(new: Any, read_old: Callable[[], Any]) -> None:
    """Raise ``ValueError`` when ``new`` sets a credential key to a mask that
    the config on disk did not already hold at the same path."""
    masked = [(p, v) for p, v in _credential_leaves(new) if _is_mask(p, v)]
    if not masked:
        return
    try:
        old = dict(_credential_leaves(read_old()))
    except Exception:
        old = {}
    bad = [p for p, v in masked if old.get(p) != v]
    if bad:
        shown = ", ".join(bad[:5]) + (f", +{len(bad) - 5} more" if len(bad) > 5 else "")
        raise ValueError(
            f"Refusing to save redacted credential mask(s) at {shown}: a masked "
            "display value is not the credential. Enter the real secret or a "
            "${ENV_VAR} reference."
        )
