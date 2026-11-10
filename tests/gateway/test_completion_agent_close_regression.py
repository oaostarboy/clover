"""Regression: tearing down a gateway agent must not end the live route session.

AIAgent.close() finalizes its session row as ``agent_close``. Gateway cleanup
of a cached agent whose session is still the route's current session used to
trigger that, so completion ownership then saw an ended tip with no successor
(RETRY ``owner_ended_no_successor``) and the async-delegation result burned all
8 delivery attempts and was dropped.
"""

import pytest

from gateway.completion_ownership import DELIVER, Route, resolve_owner
from gateway.run import GatewayRunner
from run_agent import AIAgent


def _bare_agent(db, session_id):
    agent = object.__new__(AIAgent)
    agent._session_db = db
    agent.session_id = session_id
    agent._end_session_on_close = True  # AIAgent default
    return agent


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path))
    from clover_state import SessionDB

    d = SessionDB(db_path=tmp_path / "state.db")
    yield d
    d.close()


def _route(sid):
    return Route(None, "agent:main:telegram:dm:1", sid, "telegram", "1", None)


@pytest.mark.asyncio
async def test_gateway_cleanup_keeps_live_route_session_open(db):
    db.create_session("live1", source="telegram")
    runner = object.__new__(GatewayRunner)

    runner._cleanup_agent_resources(_bare_agent(db, "live1"))

    row = db.get_session("live1")
    assert not row.get("ended_at"), row.get("end_reason")
    res = await resolve_owner("live1", _route("live1"), db=db)
    assert res.verdict == DELIVER


@pytest.mark.asyncio
async def test_owning_cleanup_still_finalizes_throwaway_session(db):
    db.create_session("bg1", source="telegram")
    runner = object.__new__(GatewayRunner)

    runner._cleanup_agent_resources(_bare_agent(db, "bg1"), finalize_session=True)

    assert db.get_session("bg1")["end_reason"] == "agent_close"


@pytest.mark.asyncio
async def test_user_reset_boundary_still_fails_closed(db):
    db.create_session("old1", source="telegram")
    runner = object.__new__(GatewayRunner)
    runner._cleanup_agent_resources(_bare_agent(db, "old1"))
    db.promote_to_session_reset("old1", "session_reset")
    db.create_session("new1", source="telegram")

    res = await resolve_owner("old1", _route("new1"), db=db)
    assert res.verdict != DELIVER


@pytest.mark.asyncio
async def test_retry_verdict_backs_off_without_burning_attempts(db, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from gateway.completion_ownership import RETRY, Resolution
    from tools import async_delegation as ad

    calls = []
    monkeypatch.setattr(ad, "claim_completion_delivery", lambda d, c: calls.append(d) or True)
    monkeypatch.setattr(ad, "release_completion_delivery", lambda d, c: True)

    runner = object.__new__(GatewayRunner)
    runner._completion_delivery_identity = lambda evt: None
    runner._preflight_completion = AsyncMock(
        return_value=(object(), Resolution("r", "t", RETRY, "owner_ended_no_successor"))
    )
    evt = {"type": "async_delegation", "delegation_id": "deleg_x", "parent_session_id": "p"}

    for _ in range(20):
        assert await runner._deliver_completion_notification("done", evt) is False
    assert len(calls) == 1  # one claim; the rest wait out the backoff
