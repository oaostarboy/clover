"""``delegation.router.mode: main_decides``: no pre-message router call.

Real ``plan_for_turn`` / backstop / ``delegate_task`` against a scratch
``CLOVER_HOME``; only the auxiliary model and a helper's conversation are fake.
"""
from __future__ import annotations

import json
import threading

import pytest
import yaml

import tools.async_delegation as ad
from agent import delegation_router as dr
from agent import foreground_time_limit as fl
from agent import router_backstop as rb
from tests.agent.test_delegation_checkpoint import run_batch
from tests.agent.test_delegation_router import aux, T, verdict_json  # noqa: F401
from tests.agent.test_router_sol_review import (  # noqa: F401  (fixtures)
    children, is_handoff, isolated, root_agent, writes,
)

pytestmark = pytest.mark.real_delegation_router


def _set_mode(monkeypatch, mode):
    from clover_constants import get_clover_home

    monkeypatch.delenv('CLOVER_IGNORE_USER_CONFIG', raising=False)
    (get_clover_home() / 'config.yaml').write_text(
        yaml.safe_dump({'delegation': {'router': {'mode': mode}}}))


def _turn(agent, message):
    fl.begin_turn(agent, None)
    return dr.plan_for_turn(agent, message, [], None)


def _router_calls(aux):
    return [c for c in getattr(aux, 'calls', [])]


def test_mode_defaults_to_router_and_is_in_the_shipped_config(monkeypatch):
    monkeypatch.setenv('CLOVER_IGNORE_USER_CONFIG', '1')
    assert dr.load_settings().mode == 'router'
    from clover_cli.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG['delegation']['router']['mode'] == 'router'


@pytest.mark.parametrize('raw,expected', [
    ('main_decides', 'main_decides'), ('Main-Decides', 'main_decides'),
    ('router', 'router'), ('nonsense', 'router'), (None, 'router'), (3, 'router'),
])
def test_mode_parsing_falls_back_to_router(monkeypatch, raw, expected):
    _set_mode(monkeypatch, raw)
    assert dr.load_settings().mode == expected


def test_main_decides_makes_zero_router_calls_and_adds_no_note(
        monkeypatch, aux, tmp_path, isolated):
    _set_mode(monkeypatch, 'main_decides')
    agent = root_agent([], tmp_path)
    before = len(_router_calls(aux))
    note = _turn(agent, 'Audit the whole repo, fix what you find, and write a report.')
    assert note == ''
    assert len(_router_calls(aux)) == before == 0


def test_main_decides_arms_the_direct_backstop_without_a_verdict(
        monkeypatch, aux, tmp_path, isolated):
    _set_mode(monkeypatch, 'main_decides')
    agent = root_agent([], tmp_path)
    _turn(agent, 'Look into why the build is slow.')
    state = rb._state(agent)
    assert state is not None and state.direct is True and state.planned is False


def test_main_decides_direct_backstop_hands_off_at_the_direct_limit(
        monkeypatch, aux, tmp_path, children, isolated):
    _set_mode(monkeypatch, 'main_decides')
    agent = root_agent([], tmp_path)
    _turn(agent, 'Look into why the build is slow.')
    rows = run_batch(agent, writes(tmp_path, 26))
    assert not any(is_handoff(r) for r in rows[:25])
    assert any(is_handoff(r) for r in rows[25:])


def test_explicit_helper_request_arms_the_tight_limits(
        monkeypatch, aux, tmp_path, children, isolated):
    _set_mode(monkeypatch, 'main_decides')
    agent = root_agent([], tmp_path)
    note = _turn(agent, 'Use helpers to audit both servers.')
    assert note == '' and len(_router_calls(aux)) == 0
    state = rb._state(agent)
    assert state.planned is True and state.direct is False
    rows = run_batch(agent, writes(tmp_path, 3))
    assert any(is_handoff(r) for r in rows)


def test_reply_quote_mentioning_helpers_is_not_an_override(
        monkeypatch, aux, tmp_path, isolated):
    _set_mode(monkeypatch, 'main_decides')
    agent = root_agent([], tmp_path)
    msg = '[Replying to: "I will use helpers for this"]\n\nExplain better.'
    _turn(agent, msg)
    state = rb._state(agent)
    assert state.direct is True and state.planned is False


def test_do_it_yourself_is_never_handed_off(monkeypatch, aux, tmp_path, children, isolated):
    _set_mode(monkeypatch, 'main_decides')
    agent = root_agent([], tmp_path)
    _turn(agent, 'Do it yourself, no helpers: tidy these files.')
    rows = run_batch(agent, writes(tmp_path, 30))
    assert not any(is_handoff(r) for r in rows)


def test_router_mode_still_calls_the_router(monkeypatch, aux, tmp_path, isolated):
    _set_mode(monkeypatch, 'router')
    aux.replies[:] = [verdict_json('direct')]
    agent = root_agent([], tmp_path)
    note = _turn(agent, 'Audit the whole repo, fix what you find, and write a report.')
    assert 'work plan' in note
    assert len(aux.calls) >= 1


# ── capacity: a message, never a blocked chat ──────────────────────────────

def test_at_capacity_returns_a_message_and_runs_nothing(monkeypatch, tmp_path, isolated):
    import tools.delegate_tool as dt

    ad._reset_for_tests()
    monkeypatch.setattr(dt, '_get_max_async_children', lambda: 1)
    release = threading.Event()
    held = ad.dispatch_async_delegation_batch(
        goals=['Audit the DNS records'], context=None, toolsets=None, role='leaf',
        model='m', session_key='',
        runner=lambda: (release.wait(30), {'results': []})[1], max_async_children=1)
    assert held['status'] == 'dispatched'
    try:
        agent = root_agent([], tmp_path)
        fl.begin_turn(agent, None)  # a real chat turn: time-limited, so strict background
        out = json.loads(dt.delegate_task(
            parent_agent=agent, goal='Build the whole explainer and verify it.',
            background=True))
    finally:
        release.set()
        ad._reset_for_tests()
    assert out['status'] == 'rejected' and out['started'] is False
    assert out['error'].startswith('at the limit: 1 running')
    assert 'Audit the DNS records' in out['error']
    assert 'did NOT run in this chat' in out['note']


# ── the model-facing text ──────────────────────────────────────────────────

def test_tool_description_and_guidance_carry_the_new_rules():
    from agent.prompt_builder import DELEGATION_MODE_GUIDANCE as g
    from tools.delegate_tool import _build_top_level_description as d

    desc = d().lower()
    for phrase in ('stays free', 'one call', 'binding', 'one short line', 'poll'):
        assert phrase in desc, phrase
    low = g.lower()
    for phrase in ('stays free', 'one call', 'binding', 'one short line', "don't poll"):
        assert phrase in low, phrase
    assert d() == d() and len(d()) <= 3000
