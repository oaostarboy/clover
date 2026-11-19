"""The dispatch guard refuses a duplicate of the SAME request, never a new job."""
from types import SimpleNamespace

import pytest

from agent.delegation_ownership import claim_dispatch, settle_dispatch


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path))
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    return SimpleNamespace(session_id="sess-1", _interrupt_requested=False)


def test_new_user_message_is_not_blocked_by_a_running_earlier_job(agent):
    tok1, claim1 = claim_dispatch(agent, "turn-1")
    assert tok1
    settle_dispatch(claim1, tok1, {"ok": True, "delegation_id": "deleg_1"})  # still running
    tok2, claim2 = claim_dispatch(agent, "turn-2")  # Ant asks for a separate job
    assert tok2, "a new request must get its own claim while an older helper runs"
    assert claim2 != claim1


def test_same_request_twice_is_refused_as_a_duplicate(agent):
    tok1, claim1 = claim_dispatch(agent, "turn-1")
    assert tok1
    settle_dispatch(claim1, tok1, {"ok": True, "delegation_id": "deleg_1"})
    tok_dup, info = claim_dispatch(agent, "turn-1")
    assert tok_dup == "" and info["delegation_id"] == "deleg_1"
