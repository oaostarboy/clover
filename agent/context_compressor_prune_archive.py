"""Archive-before-stub for tool-result prunes and compactions.

Every tool result a prune or compaction shortens is first written, byte-verified, to
``$CLOVER_HOME/cache/pruned/`` (see ``tools.tool_result_storage.write_pruned_original``) and its
stub names the file, so the model (or the summarizer) can ``read_file`` the exact output instead of
re-running the tool. A result whose write fails keeps its full body; non-local backends keep the
plain stubs. Rewrites touch only the rows the caller is already replacing, never past context.
"""

from __future__ import annotations

import json
import os
from typing import Any

from tools.tool_result_storage import (
    ARCHIVE_POINTER_SEP,
    _backend_reads_host_files,
    has_archive_pointer,
    write_pruned_original,
)


def _with_pointer(content: Any, display_path: str) -> Any:
    pointer = f"{ARCHIVE_POINTER_SEP}{display_path}"
    if isinstance(content, list):
        return [*content, {"type": "text", "text": pointer.strip(" —")}]
    return f"{content}{pointer}"


def _tool_calls_by_id(messages: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """tool_call_id -> (tool name, raw arguments) over every assistant tool call."""
    out: dict[str, tuple[str, str]] = {}
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                out[str(tc.get("id") or "")] = (fn.get("name", "unknown"), fn.get("arguments", ""))
            else:
                fn = getattr(tc, "function", None)
                out[str(getattr(tc, "id", "") or "")] = (
                    getattr(fn, "name", "unknown") if fn else "unknown",
                    getattr(fn, "arguments", "") if fn else "",
                )
    return out


def archive_pruned_originals(
    messages: list[dict[str, Any]], pruned: list[dict[str, Any]], session_id: str,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Write the original of every tool row *pruned* changed.

    Returns ``(rows, written host paths, reverted)``. A changed row whose write fails is put back
    to its original (``reverted`` counts them) so no stub ever lacks a file. A row that already
    names its archive is left as the prune made it.
    """
    call_id_to_tool = _tool_calls_by_id(messages)
    rows, written, reverted = list(pruned), [], 0
    for i, (original, replacement) in enumerate(zip(messages, pruned)):
        if (not isinstance(original, dict) or original.get("role") != "tool"
                or not isinstance(replacement, dict)
                or replacement.get("content") == original.get("content")):
            continue
        body = original.get("content")
        if isinstance(body, str) and has_archive_pointer(body):
            continue
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        call_id = str(original.get("tool_call_id") or "")
        tool_name = call_id_to_tool.get(call_id, ("unknown", ""))[0]
        archived = write_pruned_original(text, session_id or "session", tool_name, call_id)
        if archived is None:
            rows[i], reverted = dict(original), reverted + 1
            continue
        written.append(archived[0])
        rows[i] = {**replacement, "content": _with_pointer(replacement.get("content"), archived[1])}
    return rows, written, reverted


def archive_compaction_drops(
    messages: list[dict[str, Any]], dropped: list[dict[str, Any]], session_id: str,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """:func:`archive_pruned_originals` for a compaction. Sandboxed backends keep plain stubs."""
    if not _backend_reads_host_files():
        return dropped, [], 0
    return archive_pruned_originals(messages, dropped, session_id)


def discard_archives(paths: list[str]) -> None:
    """Unlink archives written for a prune that did not commit (nothing points at them)."""
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass  # unreferenced: the retention sweep reclaims it
