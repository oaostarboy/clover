"""First-run welcome in the TUI: who sees it, and that it only shows once."""
import pytest

from tui_gateway import server


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("clover_cli.config.get_clover_home", lambda: tmp_path)
    monkeypatch.setattr("tui_gateway.server._clover_home", tmp_path)
    (tmp_path / "config.yaml").write_text("model:\n  default: x\n")
    server._cfg_cache = None
    return tmp_path


class _DB:
    def __init__(self, n):
        self.n = n

    def session_count(self, **_):
        return self.n

    def get_session(self, sid):
        return {"id": sid} if self.n else None


def _db(monkeypatch, n):
    from contextlib import contextmanager

    @contextmanager
    def fake(_params=None):
        yield _DB(n)

    monkeypatch.setattr("tui_gateway.server._profile_db", fake)


def _call(method, params=None):
    return server.handle_request({"id": "1", "method": method, "params": params or {}})["result"]


def test_new_user_sees_welcome(home, monkeypatch):
    _db(monkeypatch, 1)  # only the session we just opened
    assert _call("onboarding.status", {"session_id": "s"}) == {"show": True, "hint": False}


def test_existing_user_gets_one_line_hint_once(home, monkeypatch):
    _db(monkeypatch, 5)
    assert _call("onboarding.status", {"session_id": "s"}) == {"show": False, "hint": True}
    # flag was written, so the hint never repeats
    assert _call("onboarding.status", {"session_id": "s"}) == {"show": False, "hint": False}


def test_done_sets_flag_in_config_yaml(home, monkeypatch):
    _db(monkeypatch, 1)
    _call("onboarding.done")
    assert "seen: true" in (home / "config.yaml").read_text()
    assert _call("onboarding.status", {"session_id": "s"}) == {"show": False, "hint": False}


def test_quick_start_handoff_skips_say_hi(monkeypatch):
    from clover_cli import setup

    called = []
    monkeypatch.setattr("clover_cli.lucky.offer_hi", lambda name: called.append(name) or True)
    monkeypatch.setattr(setup, "HANDOFF_TO_TUI", True)
    setup._offer_first_chat({})
    assert called == []
