"""Quick start: Esc/Left goes back one step; too-small local models are refused."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from clover_cli import lucky, model_setup_flows as msf, setup as setup_mod


def test_esc_on_provider_list_goes_back_to_name_step():
    """GoBack from the provider list must reach _run_quick_start, not be eaten."""
    calls = []

    def fake_name(config, lucky_theme=False):
        calls.append("name")

    attempts = {"n": 0}

    def fake_provider(config, short_list=False):
        attempts["n"] += 1
        calls.append("provider")
        if attempts["n"] == 1:
            raise lucky.GoBack()

    with patch.object(setup_mod, "setup_agent_name", fake_name), \
         patch.object(setup_mod, "setup_model_provider", fake_provider), \
         patch.object(setup_mod, "_apply_default_agent_settings"), \
         patch.object(setup_mod, "save_config"), \
         patch.object(setup_mod, "_print_setup_summary"):
        setup_mod._run_quick_start({}, "/nonexistent")
    assert calls == ["name", "provider", "name", "provider"]


def test_setup_model_provider_lets_goback_through(monkeypatch):
    def boom(*a, **k):
        raise lucky.GoBack()

    monkeypatch.setattr("clover_cli.main.select_provider_and_model", boom)
    with pytest.raises(lucky.GoBack):
        setup_mod.setup_model_provider({}, short_list=True)


def test_back_inside_provider_flow_returns_to_provider_list(monkeypatch):
    seen = {"n": 0}

    def flaky(*a, **k):
        seen["n"] += 1
        if seen["n"] == 1:
            raise lucky.GoBackToProviders()

    monkeypatch.setattr("clover_cli.main.select_provider_and_model", flaky)
    monkeypatch.setattr(setup_mod, "load_config", lambda: {}, raising=False)
    with patch("clover_cli.config.load_config", return_value={}):
        setup_mod.setup_model_provider({}, short_list=True)
    assert seen["n"] == 2


@pytest.mark.parametrize("key", ["esc", "left"])
def test_select_with_back_raises_goback_on_esc_and_left(monkeypatch, key):
    monkeypatch.setattr(lucky, "_can_key", lambda: True)
    keys = iter([key])
    monkeypatch.setattr(lucky, "_read_key", lambda: next(keys))
    import contextlib
    monkeypatch.setattr(lucky, "_raw", contextlib.nullcontext)
    with pytest.raises(lucky.GoBack):
        lucky.select("t", ["a", "b"], back=True)


def test_small_model_is_refused_and_bigger_one_accepted(monkeypatch):
    sizes = {"tiny": 32768, "big": 131072}
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda model, **kw: sizes[model],
    )
    picks = iter([0, 0])
    shown = []
    monkeypatch.setattr(lucky, "rail", lambda t="", **k: shown.append(t) or 1)
    monkeypatch.setattr(lucky, "gap", lambda: None)
    monkeypatch.setattr(
        "clover_cli.main._prompt_provider_choice",
        lambda choices, **k: next(picks) if len(choices) > 1 else 0,
    )
    name = msf._quick_pick_custom_model(["tiny", "big"], "http://x/v1", "")
    assert name == "big"
    assert any("too small for Clover" in t for t in shown)


def test_unknown_context_does_not_block(monkeypatch):
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda model, **kw: (_ for _ in ()).throw(RuntimeError("no probe")),
    )
    assert msf._custom_model_context_problem("m", "http://x/v1", "") is None


def test_min_context_error_names_clover_not_an_author():
    import inspect
    from agent import agent_init

    src = inspect.getsource(agent_init)
    assert "below the minimum" in src
    assert "required \"\n            f\"by Clover." in src
