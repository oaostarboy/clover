"""Pin validation for historical rollback reader."""
import subprocess

import pytest

import legacy156_checks


def test_rollback_reader_uses_verified_remote_release_not_local_tag(tmp_path, monkeypatch):
    commands = []
    def capture(args, **kwargs):
        commands.append(args)
        raise RuntimeError("stop after recording archive command")
    monkeypatch.setattr(subprocess, "run", capture)
    with pytest.raises(RuntimeError, match="stop after"):
        legacy156_checks.readable_by_156(tmp_path, tmp_path, "python", "session", tmp_path)
    assert commands[0][-1] == "b3cf7133dc0610b04417fb83cd5d6ef2214b0347"
