"""Prompt hint for the Clover iPhone app (session source ``mobile``)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.prompt_builder import PLATFORM_HINTS
from agent.system_prompt import build_system_prompt_parts


def _prompt(platform):
    agent = SimpleNamespace(
        load_soul_identity=False, skip_context_files=False, valid_tool_names=[],
        _task_completion_guidance=False, _tool_use_enforcement=False,
        _environment_probe=False, _kanban_worker_guidance="",
        _memory_store=None, _memory_manager=None, _platform_hint_overrides={},
        model="", provider="", pass_session_id=False, session_id="",
        platform=platform,
    )
    with (
        patch("run_agent.load_soul_md", return_value=""),
        patch("run_agent.build_environment_hints", return_value=""),
        patch("run_agent.build_context_files_prompt", return_value=""),
    ):
        parts = build_system_prompt_parts(agent)
    return "\n".join(str(v) for v in parts.values())


def test_mobile_hint_present_and_accurate():
    h = PLATFORM_HINTS["mobile"]
    assert "iPhone app" in h and "not a terminal" in h
    assert "MEDIA:/absolute/path" in h
    assert "does NOT render" in h  # markdown images / bare URLs are dropped by the app
    assert "desktop" in h


def test_mobile_hint_length_cap():
    assert len(PLATFORM_HINTS["mobile"]) <= 1200


def test_mobile_hint_injected_only_for_mobile():
    marker = PLATFORM_HINTS["mobile"][:60]
    assert marker in _prompt("mobile")
    for other in ("telegram", "cli", "tui", "desktop"):
        assert marker not in _prompt(other)
