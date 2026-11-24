"""Behaviour tests for the lucky quick-start setup path."""

import subprocess
import sys
import time

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path / ".clover"))
    return tmp_path


def test_quiet_default_prints_no_keeping_current(home, monkeypatch, capsys):
    from clover_cli import setup as s

    monkeypatch.setattr(s, "_curses_prompt_choice", lambda *a, **k: 0)
    s.prompt_choice("q", ["a", "b"], 0, quiet_default=True)
    assert "keeping current" not in capsys.readouterr().out


def test_quick_provider_menu_shows_no_warning_before_pick(home, monkeypatch, capsys):
    from clover_cli import main

    seen = {}

    def fake_pick(choices, *, default=0, title="", rail=False):
        seen["before"] = capsys.readouterr().out
        return None  # user cancels

    monkeypatch.setattr(main, "_prompt_provider_choice", fake_pick)
    for k in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    main.select_provider_and_model(short_list=True)
    assert "No inference provider" not in seen["before"]
    assert "Active provider:  none" not in seen["before"]


def test_local_quick_start_asks_no_techy_questions(home, monkeypatch):
    from clover_cli import model_setup_flows as flows
    from clover_cli import main, models

    asked = []
    monkeypatch.setattr(
        models, "probe_api_models",
        lambda key, url: {"models": ["tiny:1b"], "probed_url": url + "/models"},
    )
    monkeypatch.setattr(
        main, "_prompt_custom_api_mode_selection",
        lambda *a, **k: asked.append("api_mode"),
    )
    answers = iter(["http://127.0.0.1:11434/v1"])

    def fake_line(prompt):
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr("clover_cli.cli_output.line_input", lambda p: fake_line("Web address"))
    monkeypatch.setattr("clover_cli.secret_prompt.masked_secret_prompt", lambda p: "")
    monkeypatch.setattr("builtins.input", lambda *a: asked.append("input") or "")
    monkeypatch.setattr("clover_cli.auth._save_model_choice", lambda m: None)
    monkeypatch.setattr("clover_cli.auth.deactivate_provider", lambda: None)
    monkeypatch.setattr(main, "_save_custom_provider", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_prune_replaced_custom_model_config_credentials", lambda *a, **k: None)
    cfg = {}
    flows._model_flow_custom(cfg, quick=True)
    assert len(asked) == 1 and asked[0].startswith("Web address")
    assert cfg["model"]["base_url"] == "http://127.0.0.1:11434/v1"


def test_plain_output_has_no_ansi_and_no_delay(monkeypatch, capsys):
    from clover_cli import lucky

    monkeypatch.setenv("NO_COLOR", "1")
    start = time.monotonic()
    lucky.banner()
    lucky.grow()
    lucky.rail("hello")
    lucky.step_done(lucky.leaf_title(2, "Pick its brain"), "ChatGPT")
    lucky.flourish("done")
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "Leaf 2 of 3" in out
    assert "┌" in out and "└" in out
    assert time.monotonic() - start < 0.3


def test_ci_disables_animation(monkeypatch):
    from clover_cli import lucky

    monkeypatch.setenv("CI", "1")
    assert lucky.animation_ok() is False


def test_quick_start_never_prints_skipped_line(home, monkeypatch, capsys):
    from clover_cli import setup as s

    monkeypatch.setattr(s, "_curses_prompt_choice", lambda *a, **k: 0)
    monkeypatch.setattr(s, "is_noninteractive", lambda: False)
    s._IN_QUICK_START = True
    try:
        s.prompt_choice("pick", ["a", "b"], 0)
    finally:
        s._IN_QUICK_START = False
    assert "Skipped (keeping current)" not in capsys.readouterr().out


def test_quick_url_step_uses_plain_words(home, monkeypatch, capsys):
    from clover_cli import model_setup_flows as flows

    monkeypatch.setattr("clover_cli.cli_output.line_input", lambda p: "")
    monkeypatch.setattr("clover_cli.secret_prompt.masked_secret_prompt", lambda p: "")
    flows._model_flow_custom({}, quick=True)
    out = capsys.readouterr().out
    assert "OpenAI-compatible" not in out and "web address" in out


def test_select_numbered_fallback_returns_index(monkeypatch, capsys):
    from clover_cli import lucky

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    idx = lucky.select("Pick", ["A  ·  one", "B  ·  two", "C"], 0)
    out = capsys.readouterr().out
    assert idx == 1
    assert "\x1b" not in out and "B" in out


def test_select_default_on_empty_and_skip_on_eof(monkeypatch):
    from clover_cli import lucky

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert lucky.select("Pick", ["A", "B"], 1) == 1

    def boom(*a):
        raise EOFError

    monkeypatch.setattr("builtins.input", boom)
    assert lucky.select("Pick", ["A", "B"], 0) is None


def test_ascii_fallback_has_no_box_characters(monkeypatch, capsys):
    from clover_cli import lucky

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("CLOVER_ASCII", "1")
    lucky.intro("hi")
    lucky.step_done("Q", "A")
    lucky.outro("bye")
    out = capsys.readouterr().out
    assert out.isascii() or "🍀" in out
    assert not any(c in out for c in "┌│└◆◇●○")
