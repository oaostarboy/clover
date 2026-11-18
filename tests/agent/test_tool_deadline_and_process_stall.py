"""Per-tool declared deadline (upstream e1ac37eba1) + external-process stall budget (72a8b0e777)."""
import threading
import time
from types import SimpleNamespace

import pytest

from agent import tool_executor
from agent.tool_executor import (
    _ManagedToolResult, _ToolTimeoutResult, _deadline_with_floor,
    _run_sequential_tool_execution_middleware,
)
from tools.registry import registry


class _FakeAgent:
    def __init__(self):
        self._tool_worker_threads = set()
        self._tool_worker_threads_lock = threading.Lock()
        self._interrupt_requested = False

    def _touch_activity(self, msg):
        pass


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(tool_executor, "_SEQUENTIAL_INTERRUPT_POLL_SECONDS", 0.05)
    monkeypatch.setattr(tool_executor, "_emit_terminal_post_tool_call", lambda agent, **kw: None)


@pytest.fixture()
def slow_tool():
    declared = {"floor": None}
    name = "spec_slow_tool_21"
    registry.register(name=name, toolset="testing", schema={"name": name, "parameters": {}},
                      handler=lambda **kw: "x", emoji="t",
                      deadline_floor=lambda args: declared["floor"])
    try:
        yield name, declared
    finally:
        registry.deregister(name)


def _run(name, monkeypatch, generic, work):
    monkeypatch.setattr(tool_executor, "_resolve_sequential_tool_timeout", lambda: generic)

    def mw(agent, **kw):
        time.sleep(work)
        return _ManagedToolResult(result="real result", args={}, middleware_trace=[],
                                  blocked=False, dispatched=True)

    monkeypatch.setattr(tool_executor, "_run_agent_tool_execution_middleware", mw)
    return _run_sequential_tool_execution_middleware(
        _FakeAgent(), function_name=name, function_args={}, effective_task_id="t",
        tool_call_id="c", execute=None)


def test_undeclared_tool_still_hits_generic_deadline(monkeypatch, slow_tool):
    r = _run(slow_tool[0], monkeypatch, 0.3, 3)
    assert isinstance(r.result, _ToolTimeoutResult)


def test_declared_floor_honored_over_generic(monkeypatch, slow_tool):
    name, d = slow_tool
    d["floor"] = 2.0  # scaled "600s" request under a scaled "420s" generic cap
    r = _run(name, monkeypatch, 0.3, 1.0)
    assert r.result == "real result"


def test_floor_clamped_by_config_max(monkeypatch, slow_tool):
    name, d = slow_tool
    d["floor"] = 9999.0
    monkeypatch.setattr("agent.deadline._timeouts_section",
                        lambda: {"tools": {"declared_floor_max": 10}})
    f = tool_executor._declared_deadline_floor(name, {})
    assert f == 10 + tool_executor._DECLARED_DEADLINE_GRACE_S


def test_deadline_with_floor_rules():
    assert _deadline_with_floor(420.0, 605.0) == 605.0
    assert _deadline_with_floor(900.0, 605.0) == 900.0
    assert _deadline_with_floor(None, 605.0) is None


@pytest.mark.parametrize("bad", [0, -1, "nope", float("inf"), float("nan")])
def test_bad_declarations_ignored(slow_tool, bad):
    name, d = slow_tool
    d["floor"] = bad
    assert registry.get_deadline_floor(name, {}) is None


def test_terminal_600_request_is_honored(monkeypatch):
    from tools.terminal_tool import _terminal_deadline_floor
    monkeypatch.setenv("TERMINAL_TIMEOUT", "180")
    assert _terminal_deadline_floor({"command": "x", "timeout": 600}) == 600.0
    f = tool_executor._declared_deadline_floor("terminal", {"command": "x", "timeout": 600})
    assert _deadline_with_floor(420.0, f) > 600.0
    assert _terminal_deadline_floor({"command": "x", "background": True, "timeout": 600}) is None


def test_process_wait_floor(monkeypatch):
    from tools.process_registry_wait import process_deadline_floor
    monkeypatch.setenv("TERMINAL_TIMEOUT", "600")
    assert process_deadline_floor({"action": "wait", "timeout": 900}) == 600.0
    assert process_deadline_floor({"action": "poll"}) is None


def test_terminal_partial_output_on_own_timeout():
    """Real terminal tool: its own timeout returns partial output + 124."""
    import json, os
    from tools.terminal_tool import terminal_tool
    out = json.loads(terminal_tool(
        command="echo partial-line; sleep 30", timeout=2, task_id="t21-partial"))
    assert "partial-line" in out["output"]
    assert out["exit_code"] == 124


# ---- external-process stall budget -------------------------------------

def _agent(provider, explicit=False, req=1800.0):
    return SimpleNamespace(
        provider=provider, _stale_timeout_is_explicit=lambda: explicit,
        _resolved_api_call_timeout=lambda: req,
        run_budget_seconds=None, _run_budget_started_at=None)


def test_external_process_provider_detected_by_auth_type(monkeypatch):
    from agent.process_transport import is_external_process_provider
    import clover_cli.auth as auth
    assert is_external_process_provider("copilot-acp")
    assert not is_external_process_provider("copilot")
    fake = SimpleNamespace(auth_type="external_process")
    monkeypatch.setitem(auth.PROVIDER_REGISTRY, "agy-fake", fake)
    assert is_external_process_provider("agy-fake")


def test_external_process_not_killed_by_cloud_stale_timeout():
    from agent.process_transport import implicit_non_stream_stale_timeout as f
    assert f(_agent("copilot-acp")) == 1800.0
    assert f(_agent("copilot-acp", req=7.0)) == 7.0          # still finite: truly stalled child is killed
    assert f(_agent("copilot-acp", explicit=True)) is None   # explicit wins
    assert f(_agent("openrouter")) is None


def test_agent_compute_uses_external_budget():
    from run_agent import AIAgent
    a = AIAgent(model="x", provider="copilot-acp", api_key="k", base_url="acp://copilot",
                quiet_mode=True, skip_context_files=True, skip_memory=True, platform="cli")
    t = a._compute_non_stream_stale_timeout([{"role": "user", "content": "hi"}])
    assert t >= 1800.0 or t == a._resolved_api_call_timeout()
    b = AIAgent(model="gpt-5.5", provider="openai-codex", api_key="k",
                base_url="https://chatgpt.com/backend-api/codex",
                quiet_mode=True, skip_context_files=True, skip_memory=True, platform="cli")
    assert b._compute_non_stream_stale_timeout([{"role": "user", "content": "hi"}]) < 1000


# ---- real subprocess: stalled vs producing external-process child --------

_FAKE_ACP = r'''
import json, sys, time
mode = sys.argv[1]
for line in sys.stdin:
    m = json.loads(line)
    rid, meth = m.get("id"), m.get("method")
    def reply(res): print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": res}), flush=True)
    if meth == "initialize":
        reply({})
    elif meth == "session/new":
        reply({"sessionId": "s1"})
    elif meth == "session/prompt":
        if mode == "stall":
            print(json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {
                "sessionUpdate": "agent_message_chunk", "content": {"text": "partial"}}}}), flush=True)
            time.sleep(3600)
        for i in range(8):  # 8 x 0.4s = 3.2s total, each gap < budget
            print(json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {
                "sessionUpdate": "agent_message_chunk", "content": {"text": "x"}}}}), flush=True)
            time.sleep(0.4)
        reply({"stopReason": "end_turn"})
'''


def _acp_client(tmp_path, mode):
    import sys
    from agent.copilot_acp_client import CopilotACPClient
    script = tmp_path / "fake_acp.py"
    script.write_text(_FAKE_ACP)
    return CopilotACPClient(acp_command=sys.executable, acp_args=[str(script), mode],
                            acp_cwd=str(tmp_path))


def test_stalled_external_process_is_killed_within_budget(tmp_path):
    client = _acp_client(tmp_path, "stall")
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        client._run_prompt("hi", timeout_seconds=1.0)
    assert time.monotonic() - t0 < 6.0
    assert client._active_process is None or client._active_process.poll() is not None


def test_producing_external_process_is_not_killed(tmp_path):
    client = _acp_client(tmp_path, "produce")
    t0 = time.monotonic()
    text, _ = client._run_prompt("hi", timeout_seconds=1.5)  # total run (3.2s) > budget
    assert text == "x" * 8
    assert time.monotonic() - t0 > 1.5
