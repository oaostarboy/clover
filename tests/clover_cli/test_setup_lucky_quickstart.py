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

    def fake_pick(choices, *, default=0, title="", rail=False, back=False):
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


def _keys(monkeypatch, lucky, seq):
    it = iter(seq)
    monkeypatch.setattr(lucky, "_read_key", lambda: next(it))
    monkeypatch.setattr(lucky, "_can_key", lambda: True)

    class _NoRaw:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(lucky, "_raw", _NoRaw)
    monkeypatch.setattr(lucky, "_tty", lambda: False)


def test_select_arrows_pick_index(monkeypatch, capsys):
    from clover_cli import lucky

    _keys(monkeypatch, lucky, ["down", "down", "up", "enter"])
    assert lucky.select("t", ["a", "b", "c"], 0) == 1


def test_select_esc_skips_and_back_raises(monkeypatch, capsys):
    from clover_cli import lucky

    _keys(monkeypatch, lucky, ["esc"])
    assert lucky.select("t", ["a", "b"], 0) is None
    _keys(monkeypatch, lucky, ["left"])
    with pytest.raises(lucky.GoBack):
        lucky.select("t", ["a", "b"], 0, back=True)


def test_ask_types_live_preview_and_default(monkeypatch, capsys):
    from clover_cli import lucky

    _keys(monkeypatch, lucky, list("Ada") + ["backspace", "a", "enter"])
    assert lucky.ask("n", default="Clover", preview=lucky.greeting) == "Ada"
    _keys(monkeypatch, lucky, ["enter"])
    assert lucky.ask("n", default="Clover") == ""


def test_ask_esc_goes_back_only_when_empty(monkeypatch, capsys):
    from clover_cli import lucky

    _keys(monkeypatch, lucky, ["esc"])
    with pytest.raises(lucky.GoBack):
        lucky.ask("n", back=True)


def test_windows_uses_msvcrt_path_and_unicode_rules(monkeypatch):
    from clover_cli import lucky

    monkeypatch.setattr(lucky.os, "name", "nt")
    assert lucky._use_windows_keys() is True
    for k in ("WT_SESSION", "TERM_PROGRAM", "ConEmuANSI", "CLOVER_ASCII"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TERM", "")
    monkeypatch.setattr(lucky, "_modern_conhost", lambda: False)
    assert lucky.unicode_ok() is False  # really old console -> ASCII
    monkeypatch.setattr(lucky, "_modern_conhost", lambda: True)
    assert lucky.unicode_ok() is True  # Windows 10+ conhost with ANSI on
    monkeypatch.setattr(lucky, "_modern_conhost", lambda: False)
    monkeypatch.setenv("WT_SESSION", "x")
    assert lucky.unicode_ok() is True  # Windows Terminal
    monkeypatch.setattr(lucky.os, "name", "posix")
    assert lucky._use_windows_keys() is False


def test_markers_are_box_drawing_joints_on_the_rail(monkeypatch):
    from clover_cli import lucky

    monkeypatch.delenv("CLOVER_ASCII", raising=False)
    monkeypatch.setattr(lucky, "unicode_ok", lambda: True)
    for name in ("active", "done"):
        assert lucky.S(name).startswith("├")  # same cell family as │, no gap
    assert "◇" not in lucky.S("done") and "◆" not in lucky.S("active")


def _env(monkeypatch, **kw):
    for k in ("TERM", "TERM_PROGRAM", "KITTY_WINDOW_ID", "WT_SESSION", "NO_COLOR",
              "CLOVER_ASCII", "CLOVER_IMAGE", "COLORTERM"):
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


@pytest.mark.parametrize("env,proto", [
    ({"TERM": "xterm-kitty"}, "kitty"),
    ({"KITTY_WINDOW_ID": "1", "TERM": "xterm-256color"}, "kitty"),
    ({"TERM_PROGRAM": "ghostty", "TERM": "xterm-256color"}, "kitty"),
    ({"TERM_PROGRAM": "iTerm.app", "TERM": "xterm-256color"}, "iterm"),
    ({"TERM_PROGRAM": "WezTerm", "TERM": "xterm-256color"}, "iterm"),
    ({"WT_SESSION": "x", "TERM": "xterm-256color"}, "sixel"),
    ({"TERM": "foot"}, "sixel"),
    ({"TERM": "xterm"}, None),
])
def test_image_protocol_per_env(monkeypatch, env, proto):
    from clover_cli import lucky

    _env(monkeypatch, **env)
    assert lucky.image_protocol() == proto


def test_logo_image_is_the_real_png_and_no_heart_art(monkeypatch):
    import base64
    import hashlib
    from pathlib import Path

    from clover_cli import lucky

    _env(monkeypatch, TERM="xterm-kitty")
    out = lucky.logo_image()
    assert out.startswith("\033_G") and "f=100" in out
    payload = "".join(c.split(";", 1)[1] for c in out.split("\033\\")[:-1])
    png = base64.b64decode(payload)
    assert png == (Path(lucky.__file__).parent / "data" / "clover-logo.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) < 8192
    assert not hasattr(lucky, "MARK") and not hasattr(lucky, "grow_mark")
    _env(monkeypatch, TERM_PROGRAM="iTerm.app")
    assert lucky.logo_image().startswith("\033]1337;File=inline=1")
    _env(monkeypatch, TERM="foot")
    assert lucky.logo_image().startswith("\033P")


def test_wordmark_is_solid_blocks_only():
    from clover_cli import lucky

    assert set("".join(lucky.WORDMARK)) <= set(" █▀▄")


def test_banner_plain_xterm_has_letters_only(monkeypatch, capsys):
    from clover_cli import lucky

    _env(monkeypatch, TERM="xterm")
    lucky.banner()
    out = capsys.readouterr().out
    assert "\033_G" not in out and "\033P" not in out and "1337" not in out
    assert "██" in out and "╔" not in out


def test_no_color_and_ascii_emit_no_escapes_or_blocks(monkeypatch, capsys):
    from clover_cli import lucky

    _env(monkeypatch, TERM="xterm-kitty", NO_COLOR="1", CLOVER_NO_ANIM="1")
    lucky.banner()
    lucky.flourish("done")
    assert "\033" not in capsys.readouterr().out
    _env(monkeypatch, TERM="xterm", CLOVER_ASCII="1")
    lucky.banner()
    out = capsys.readouterr().out
    assert "Clover" in out and not any(c in out for c in "█▀▄")


def test_installer_default_path_has_no_jargon():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "scripts"
    sh = (root / "install.sh").read_text()
    ps = (root / "install.ps1").read_text()
    for text in (sh, ps):
        assert "uv ready" not in text
        assert "is on your PATH" not in text
    shown = re.findall(r'log_success "([^"]*)"', sh)
    assert not [s for s in shown if re.search(r"\(\$|launcher|PATH|venv|Virtual environment|\.yaml|\.env", s)]
    assert "◇" not in sh


def test_template_config_alone_is_not_an_existing_setup(tmp_path):
    from clover_cli.setup import _config_has_provider

    cfg = tmp_path / "config.yaml"
    assert _config_has_provider(cfg) is False
    cfg.write_text("model:\n  default: some/model\nagent:\n  max_turns: 90\n")
    assert _config_has_provider(cfg) is False
    cfg.write_text("model:\n  provider: openrouter\n  default: x\n")
    assert _config_has_provider(cfg) is True
    cfg.write_text('model:\n  default: x\n  provider: "auto"\n  base_url: "https://openrouter.ai/api/v1"\n')
    assert _config_has_provider(cfg) is False  # shipped template


def _lucky_env(monkeypatch, **env):
    from clover_cli import lucky

    for k in ("COLORTERM", "TERM", "TERM_PROGRAM", "WT_SESSION", "NO_COLOR", "CI"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setattr(lucky, "should_use_color", lambda: "NO_COLOR" not in env)
    monkeypatch.setattr(lucky, "_modern_conhost", lambda: False)
    return lucky


def test_windows_terminal_gets_truecolor_gradient(monkeypatch):
    lucky = _lucky_env(monkeypatch, WT_SESSION="x", TERM="vt100")
    assert "38;2;" in lucky.gradient("CLOVER")


def test_xterm_256_without_colorterm_gets_256_gradient(monkeypatch):
    lucky = _lucky_env(monkeypatch, TERM="xterm-256color")
    out = lucky.gradient("CLOVER")
    assert "38;5;" in out and "38;2;" not in out
    assert "\033[1;32m" not in out and "\033[32m" not in out


def test_apple_terminal_uses_256(monkeypatch):
    lucky = _lucky_env(monkeypatch, TERM="xterm", TERM_PROGRAM="Apple_Terminal")
    assert "38;5;" in lucky.gradient("CLOVER")


def test_no_color_prints_plain(monkeypatch):
    lucky = _lucky_env(monkeypatch, NO_COLOR="1", TERM="xterm-256color")
    assert lucky.gradient("CLOVER") == "CLOVER"


def test_plain_xterm_term_gets_256_gradient(monkeypatch):
    lucky = _lucky_env(monkeypatch, TERM="xterm")
    out = lucky.gradient("CLOVER")
    assert "38;5;" in out and "38;2;" not in out


def test_basic_terminal_falls_back_to_basic_green(monkeypatch):
    lucky = _lucky_env(monkeypatch, TERM="vt100")
    out = lucky.gradient("CLOVER")
    assert "38;5;" not in out and "38;2;" not in out and "\033[" in out


def test_wordmark_full_blocks_only_and_one_gradient_per_column(monkeypatch):
    from clover_cli import lucky

    assert not set("".join(lucky.WORDMARK)) - {"█", " "}
    monkeypatch.setattr(lucky, "should_use_color", lambda: True)
    monkeypatch.setattr(lucky, "color_depth", lambda: 24)
    rows = lucky._wordmark_lines()
    import re

    cols = [re.findall(r"48;2;(\d+;\d+;\d+)m ", r) for r in rows]
    # same column -> same colour in every row where it is filled
    seen = {}
    for r, line in enumerate(lucky.WORDMARK):
        filled = [x for x, ch in enumerate(line) if ch == "█"]
        for x, c in zip(filled, cols[r]):
            assert seen.setdefault(x, c) == c
