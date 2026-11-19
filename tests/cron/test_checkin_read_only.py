"""Proactive check-in runs are read-only, enforced in code (not by prompt)."""
import json
import sys

import pytest

from cron import checkin_readonly as ro


@pytest.fixture()
def in_checkin():
    tok = ro.enter("check")
    yield
    ro.exit(tok)


def _call(name, args):
    from model_tools import handle_function_call
    return json.loads(handle_function_call(name, args, task_id="t"))


def test_tool_list_excludes_send_and_write_tools():
    from model_tools import get_tool_definitions
    defs = get_tool_definitions(enabled_toolsets=None, quiet_mode=True)

    class A:  # minimal stand-in for the constructed agent's tool state
        tools = defs
        valid_tool_names = set()

    removed = ro.filter_agent_tools(A)
    names = {t["function"]["name"] for t in A.tools}
    for bad in ("send_message", "write_file", "patch", "delegate_task", "cronjob",
                "memory", "skill_manage", "browser_click", "browser_type",
                "computer_use", "execute_code", "image_generate", "ha_call_service"):
        assert bad not in names
    assert {"read_file", "search_files", "terminal"} <= names | {"terminal"}
    assert "read_file" in names and "search_files" in names
    assert A.valid_tool_names == names
    assert "write_file" in removed or "write_file" not in {t["function"]["name"] for t in defs}


def test_mcp_style_names_default_deny():
    assert ro.is_read_tool("mcp__mail__search_messages")
    assert ro.is_read_tool("mcp__calendar__list_events")
    for n in ("mcp__mail__send_message", "mcp__mail__reply", "mcp__cal__create_event",
              "mcp__tasks__complete_task", "mcp__mail__delete_message",
              "mcp__mail__move_message", "mcp__mail__flag_message", "mystery_tool"):
        assert not ro.is_read_tool(n), n


@pytest.mark.parametrize("cmd", [
    "himalaya message send < draft.eml",
    "himalaya message reply 12",
    "himalaya message forward 3",
    "himalaya message move 3 Archive",
    "himalaya message delete 3",
    "himalaya flag add 3 seen",
    "gws gmail users messages send --params x",
    "gws calendar events insert --params x",
    "gws calendar events patch --params x",
    "gws calendar events delete --params x",
    "rm -rf /tmp/x",
    "echo hi > /tmp/some_file",
])
def test_mutating_terminal_commands_refused(cmd):
    assert ro.terminal_command_blocked(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "himalaya envelope list", "himalaya message read 12", "ls -la", "cat notes.txt",
    "gws gmail users messages list --params x", "gws calendar events list",
    "grep -r foo . 2>/dev/null",
])
def test_read_terminal_commands_allowed(cmd):
    assert not ro.terminal_command_blocked(cmd), cmd


def test_blocked_send_message_executes_nothing(in_checkin, monkeypatch, caplog):
    import tools.send_message_tool as smt
    called = []
    monkeypatch.setattr(smt, "send_message_tool", lambda *a, **k: called.append(1) or "{}", raising=False)
    with caplog.at_level("INFO"):
        res = _call("send_message", {"action": "send", "target": "telegram", "message": "hi"})
    assert "read-only" in json.dumps(res)
    assert called == []
    assert sum("check-in read-only: refused send_message" in r.message for r in caplog.records) == 1


def test_blocked_write_file_writes_nothing(in_checkin, tmp_path):
    target = tmp_path / "out.txt"
    res = _call("write_file", {"path": str(target), "content": "x"})
    assert "read-only" in json.dumps(res)
    assert not target.exists()


def test_blocked_himalaya_send_via_terminal_runs_nothing(in_checkin, tmp_path):
    marker = tmp_path / "ran"
    res = _call("terminal", {"command": f"touch {marker} && himalaya message send < x"})
    assert "read-only" in json.dumps(res)
    assert not marker.exists()


def test_read_calls_succeed(in_checkin, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello world")
    res = _call("read_file", {"path": str(f)})
    assert "hello world" in json.dumps(res)
    res = _call("terminal", {"command": "echo ok"})
    assert "read-only" not in json.dumps(res)


def test_tool_call_bridge_cannot_launder_a_write(in_checkin, tmp_path):
    target = tmp_path / "b.txt"
    res = _call("tool_call", {"name": "write_file", "arguments": {"path": str(target), "content": "x"}})
    assert not target.exists()


def test_non_checkin_run_unchanged(tmp_path):
    assert not ro.is_checkin_run()
    assert ro.blocked_message("send_message", {}) is None
    target = tmp_path / "c.txt"
    _call("write_file", {"path": str(target), "content": "x"})
    assert target.exists()


def test_checkin_forces_cron_approval_deny(in_checkin, monkeypatch):
    import tools.approval as ap
    assert ap._get_cron_approval_mode() == "deny"


# --- run_job wiring ----------------------------------------------------

def _stub_run_job(monkeypatch, observed):
    import cron.scheduler as sched

    class FakeAgent:
        def __init__(self, **kw):
            from model_tools import get_tool_definitions
            self.tools = get_tool_definitions(enabled_toolsets=None, quiet_mode=True)
            self.valid_tool_names = {t["function"]["name"] for t in self.tools}
            observed["init_names"] = set(self.valid_tool_names)
            observed["platform"] = kw.get("platform")

        def run_conversation(self, *_a, **_k):
            observed["run_names"] = set(self.valid_tool_names)
            observed["checkin_ctx"] = ro.is_checkin_run()
            return {"final_response": "all quiet", "messages": []}

        def get_activity_summary(self):
            return {"seconds_since_activity": 0.0}

    fake_mod = type(sys)("run_agent")
    fake_mod.AIAgent = FakeAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_mod)
    from clover_cli import runtime_provider as _rtp
    monkeypatch.setattr(_rtp, "resolve_runtime_provider", lambda **_kw: {
        "provider": "test", "api_key": "k", "base_url": "http://test.local",
        "api_mode": "chat_completions"})
    monkeypatch.setattr(sched, "_build_job_prompt", lambda job, prerun_script=None, **kw: "hi")
    monkeypatch.setattr(sched, "_resolve_origin", lambda job: None)
    monkeypatch.setattr(sched, "_resolve_delivery_target", lambda job: None)
    monkeypatch.setattr(sched, "_resolve_cron_enabled_toolsets", lambda job, cfg: None)
    monkeypatch.setenv("CLOVER_CRON_TIMEOUT", "0")
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_kw: True)


@pytest.mark.parametrize("role", ["brief", "check"])
def test_run_job_checkin_is_read_only_and_restores_context(monkeypatch, role):
    import cron.scheduler as sched
    obs = {}
    _stub_run_job(monkeypatch, obs)
    ok, *_ = sched.run_job({"id": "ci", "name": "n", "schedule_display": "manual",
                            "checkin_role": role})
    assert ok is True
    assert obs["checkin_ctx"] is True
    assert "send_message" not in obs["run_names"] and "write_file" not in obs["run_names"]
    assert "read_file" in obs["run_names"]
    assert not ro.is_checkin_run()  # context restored after the run


def test_run_job_non_checkin_tools_unchanged(monkeypatch):
    import cron.scheduler as sched
    obs = {}
    _stub_run_job(monkeypatch, obs)
    ok, *_ = sched.run_job({"id": "n1", "name": "n", "schedule_display": "manual"})
    assert ok is True
    assert obs["checkin_ctx"] is False
    assert obs["run_names"] == obs["init_names"]
