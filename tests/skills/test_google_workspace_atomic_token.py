import importlib.util
import json
import stat
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "skills/productivity/google-workspace/scripts/_secure_storage.py"


@pytest.fixture
def mod():
    spec = importlib.util.spec_from_file_location("_secure_storage_t", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_writes_0600_json(mod, tmp_path):
    p = tmp_path / "google_token.json"
    p.write_text("{}")
    p.chmod(0o644)
    mod.write_private_json(p, {"token": "a"})
    assert json.loads(p.read_text()) == {"token": "a"}
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_interrupted_write_preserves_existing(mod, tmp_path, monkeypatch):
    p = tmp_path / "google_token.json"
    p.write_text('{"token": "old"}')

    def boom(data, file, indent):
        file.write('{"tok')
        raise RuntimeError("interrupted")

    monkeypatch.setattr(mod.json, "dump", boom)
    with pytest.raises(RuntimeError):
        mod.write_private_json(p, {"token": "new"})
    assert p.read_text() == '{"token": "old"}'
    assert [x.name for x in tmp_path.iterdir() if ".tmp." in x.name] == []


def test_scripts_use_atomic_writer():
    d = SRC.parent
    for f in ("google_api.py", "gws_bridge.py", "setup.py"):
        t = (d / f).read_text()
        assert "TOKEN_PATH.write_text" not in t and "get_token_path().write_text" not in t
        assert "write_private_json" in t
