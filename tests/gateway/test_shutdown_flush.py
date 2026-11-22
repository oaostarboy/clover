"""Tests for gateway/shutdown_flush.py — pending message durability (#72680)."""

import json
import os
import stat
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gateway.shutdown_flush import (
    _serialise_value,
    flush_pending_to_file,
    recover_pending_to_db,
)


def _make_flush_dir(tmp_path: Path) -> Path:
    """Create a temp flush dir and monkeypatch _get_flush_dir to use it."""
    flush_dir = tmp_path / "pending_messages"
    flush_dir.mkdir(parents=True, exist_ok=True)
    return flush_dir


def test_flush_writes_string_pending_to_file(tmp_path, monkeypatch):
    flush_dir = _make_flush_dir(tmp_path)
    monkeypatch.setattr(
        "gateway.shutdown_flush._get_flush_dir", lambda: flush_dir
    )
    pending = {"agent:main:telegram:supergroup:123": "hello world"}
    count = flush_pending_to_file(pending, reason="shutdown")
    assert count == 1
    files = list(flush_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["session_key"] == "agent:main:telegram:supergroup:123"
    assert payload["reason"] == "shutdown"
    assert payload["data"]["text"] == "hello world"
    assert ":" not in files[0].name
    assert "telegram" not in files[0].name


def test_flush_writes_message_event_to_file(tmp_path, monkeypatch):
    flush_dir = _make_flush_dir(tmp_path)
    monkeypatch.setattr(
        "gateway.shutdown_flush._get_flush_dir", lambda: flush_dir
    )
    event = MagicMock()
    event.text = "user message"
    event.session_id = "20260728_120000_abc"
    event.platform = "telegram"
    event.sender_id = "456"
    event.sender_name = "Alice"
    event.reply_to = None
    event.media = None
    event.raw_event = None

    count = flush_pending_to_file({"session_key_1": event}, reason="adapter_shutdown")
    assert count == 1
    files = list(flush_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["data"]["text"] == "user message"
    assert payload["data"]["session_id"] == "20260728_120000_abc"


def test_recover_inserts_via_append_message_and_deletes_file(tmp_path, monkeypatch):
    flush_dir = _make_flush_dir(tmp_path)
    monkeypatch.setattr(
        "gateway.shutdown_flush._get_flush_dir", lambda: flush_dir
    )
    ts = int(time.time())
    # Write a flush file with session_id
    payload = {
        "session_key": "agent:main:telegram:supergroup:123",
        "reason": "shutdown",
        "ts": ts,
        "data": {
            "text": "lost message",
            "session_id": "20260728_120000_abc",
        },
    }
    flush_file = flush_dir / "test_session_123.json"
    flush_file.write_text(json.dumps(payload), encoding="utf-8")

    mock_db = MagicMock()
    count = recover_pending_to_db(mock_db)

    assert count == 1
    mock_db.append_message.assert_called_once_with(
        session_id="20260728_120000_abc",
        role="user",
        content="lost message",
        timestamp=ts,
    )
    assert not flush_file.exists()


def test_recover_closes_owned_db_when_unexpected_exception_escapes(
    tmp_path, monkeypatch
):
    """Owned SessionDB must close even when recovery is interrupted."""
    flush_dir = _make_flush_dir(tmp_path)
    monkeypatch.setattr(
        "gateway.shutdown_flush._get_flush_dir", lambda: flush_dir
    )
    (flush_dir / "pending.json").write_text(
        json.dumps(
            {
                "session_key": "agent:main:telegram:123",
                "data": {"text": "message", "session_id": "sid"},
            }
        ),
        encoding="utf-8",
    )

    class InterruptingDB:
        closed = False

        def append_message(self, **_kwargs):
            raise KeyboardInterrupt

        def close(self):
            self.closed = True

    db = InterruptingDB()
    monkeypatch.setattr("clover_state.SessionDB", lambda: db)

    with pytest.raises(KeyboardInterrupt):
        recover_pending_to_db()

    assert db.closed is True


def test_serialise_object_with_text():
    obj = MagicMock()
    obj.text = "msg"
    obj.session_id = "sid"
    obj.platform = None
    obj.sender_id = None
    obj.sender_name = None
    obj.reply_to = None
    obj.media = None
    obj.raw_event = None
    result = _serialise_value(obj)
    assert result is not None
    assert result["text"] == "msg"
    assert result["session_id"] == "sid"


def test_get_flush_dir_uses_get_clover_home(tmp_path, monkeypatch):
    """Flush dir must use get_clover_home(), not hardcoded Path.home()."""
    import gateway.shutdown_flush as mod

    captured = {}

    def fake_get_clover_home():
        from pathlib import Path
        captured["called"] = True
        return tmp_path

    monkeypatch.setattr(
        "clover_constants.get_clover_home", fake_get_clover_home
    )
    result = mod._get_flush_dir()
    assert captured.get("called") is True
    assert result == tmp_path / "pending_messages"




# ---------------------------------------------------------------------------
# Restart-recovery regressions
# ---------------------------------------------------------------------------


def _spool(flush_dir, name, session_key, data, ts=1, seq=None):
    payload = {"session_key": session_key, "reason": "shutdown", "ts": ts, "data": data}
    if seq is not None:
        payload["seq"] = seq
    (flush_dir / name).write_text(json.dumps(payload), encoding="utf-8")


def test_recover_continues_after_per_file_failure(tmp_path, monkeypatch):
    """One failing file must not strand later files (Exception before BaseException)."""
    flush_dir = _make_flush_dir(tmp_path)
    monkeypatch.setattr("gateway.shutdown_flush._get_flush_dir", lambda: flush_dir)
    _spool(flush_dir, "a.json", "k1", {"text": "bad", "session_id": "boom"})
    _spool(flush_dir, "b.json", "k2", {"text": "good", "session_id": "ok"})

    class DB:
        closed = False
        calls = []

        def append_message(self, **kw):
            if kw["session_id"] == "boom":
                raise RuntimeError("db hiccup")
            self.calls.append(kw)

        def close(self):
            self.closed = True

    db = DB()
    monkeypatch.setattr("clover_state.SessionDB", lambda: db)
    assert recover_pending_to_db() == 1
    assert [c["content"] for c in db.calls] == ["good"]
    assert (flush_dir / "a.json").exists()  # retained for retry
    assert not (flush_dir / "b.json").exists()
    assert db.closed is True


def test_recover_resolves_session_id_from_session_key(tmp_path, monkeypatch):
    """Text-only runner entries carry no session_id; resolve via session_key."""
    flush_dir = _make_flush_dir(tmp_path)
    monkeypatch.setattr("gateway.shutdown_flush._get_flush_dir", lambda: flush_dir)
    _spool(flush_dir, "a.json", "agent:main:telegram:dm:42", {"text": "hello"})

    db = MagicMock()
    db.find_session_id_by_key.return_value = "sid-42"
    assert recover_pending_to_db(session_db=db) == 1
    db.find_session_id_by_key.assert_called_once_with("agent:main:telegram:dm:42")
    assert db.append_message.call_args.kwargs["session_id"] == "sid-42"
    assert not (flush_dir / "a.json").exists()


def test_recover_keeps_unresolvable_text_entry(tmp_path, monkeypatch):
    flush_dir = _make_flush_dir(tmp_path)
    monkeypatch.setattr("gateway.shutdown_flush._get_flush_dir", lambda: flush_dir)
    _spool(flush_dir, "a.json", "unknown", {"text": "hello"})
    db = MagicMock()
    db.find_session_id_by_key.return_value = None
    assert recover_pending_to_db(session_db=db) == 0
    assert (flush_dir / "a.json").exists()


def test_flush_list_values_preserve_order_on_recovery(tmp_path, monkeypatch):
    flush_dir = _make_flush_dir(tmp_path)
    monkeypatch.setattr("gateway.shutdown_flush._get_flush_dir", lambda: flush_dir)
    assert flush_pending_to_file({"k": ["one", "two", "three"]}, reason="adapter_shutdown") == 3
    db = MagicMock()
    db.find_session_id_by_key.return_value = "sid"
    assert recover_pending_to_db(session_db=db) == 3
    assert [c.kwargs["content"] for c in db.append_message.call_args_list] == [
        "one", "two", "three",
    ]
