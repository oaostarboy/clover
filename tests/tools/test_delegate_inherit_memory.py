"""Delegated children inherit the parent's MEMORY.md/USER.md, read-only."""
import importlib
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path))
    mem = tmp_path / "memories"
    mem.mkdir()
    (mem / "USER.md").write_text("Logins: never ask Ant, use the Zen session\n")
    (mem / "MEMORY.md").write_text("MARKER-MEMORY-7731 project note\n")
    return tmp_path


def _agent(**kw):
    from run_agent import AIAgent
    return AIAgent(api_key="x", base_url="http://127.0.0.1:1/v1", model="m",
                   quiet_mode=True, skip_context_files=True, **kw)


def _prompt(a):
    return a._build_system_prompt()


def _child_of(parent):
    from tools import delegate_tool as d
    child = _agent(skip_memory=True, enabled_toolsets=["terminal"])
    d._attach_inherited_memory(child, parent)
    return child


def test_default_child_prompt_has_parent_memory_and_parent_unchanged(home):
    parent = _agent(enabled_toolsets=["terminal"])
    before = _prompt(parent)
    child = _child_of(parent)
    cp = _prompt(child)
    assert "Logins: never ask Ant" in cp and "MARKER-MEMORY-7731" in cp
    # parent prompt unchanged (ignore the clock line, which is time-dependent)
    strip = lambda t: t.split("Conversation started:")[0]
    assert strip(_prompt(parent)) == strip(before)


def test_inherit_false_is_clean_slate(home, monkeypatch):
    from tools import delegate_tool as d
    parent = _agent(enabled_toolsets=["terminal"])
    monkeypatch.setattr(d, "_load_config", lambda: {"inherit_memory": False})
    cp = _prompt(_child_of(parent))
    assert "Logins: never ask Ant" not in cp and "MARKER-MEMORY-7731" not in cp


def test_child_cannot_write_memory(home):
    from tools import delegate_tool as d
    parent = _agent(enabled_toolsets=["terminal"])
    child = _child_of(parent)
    assert "memory" in d.DELEGATE_BLOCKED_TOOLS
    assert not hasattr(child._memory_store, "add")
    assert "memory" not in child.valid_tool_names
    before = (home / "memories" / "USER.md").read_text()
    assert (home / "memories" / "USER.md").read_text() == before


def test_parent_without_store_still_inherits_from_disk(home):
    from tools import delegate_tool as d
    parent = MagicMock(spec=[])  # no _memory_store attribute
    child = _agent(skip_memory=True, enabled_toolsets=["terminal"])
    d._attach_inherited_memory(child, parent)
    assert "MARKER-MEMORY-7731" in _prompt(child)
