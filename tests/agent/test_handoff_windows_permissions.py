"""Handoff files must remain writable on Windows without os.fchmod."""
import os
from types import SimpleNamespace

from agent import handoff_notes


def test_write_notes_without_windows_fchmod(tmp_path, monkeypatch):
    # Override this module's os binding only; don't alter the host interpreter.
    proxy = SimpleNamespace(**{name: getattr(os, name) for name in dir(os) if name != 'fchmod'})
    proxy.name = 'nt'
    monkeypatch.setattr(handoff_notes, 'os', proxy)
    monkeypatch.setattr(handoff_notes, 'notes_dir', lambda: tmp_path / 'handoff')
    target = handoff_notes.write_notes('test-id', 'safe handoff')
    assert target is not None
    assert target.read_text(encoding='utf-8') == 'safe handoff'
