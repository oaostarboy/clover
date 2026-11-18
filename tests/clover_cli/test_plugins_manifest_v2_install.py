"""Installer accepts manifest_version 2 (same ceiling as the loader)."""
import os
import shutil
import subprocess as sp

import pytest

from clover_cli import plugins_cmd as pc
from clover_cli.plugins import SUPPORTED_MANIFEST_VERSION


def _repo(root, manifest_text):
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(manifest_text, encoding="utf-8")
    (root / "__init__.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "i"]):
        sp.run(cmd, cwd=root, check=True, env=env)


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    d = tmp_path / "installed"
    d.mkdir()
    monkeypatch.setattr(pc, "_plugins_dir", lambda: d)
    return d


def test_installer_ceiling_matches_loader():
    assert pc._SUPPORTED_MANIFEST_VERSION == SUPPORTED_MANIFEST_VERSION >= 2


@pytest.mark.parametrize("mv", ["", "manifest_version: 1\n", "manifest_version: 2\n"])
def test_v1_and_v2_install(tmp_path, plugins_dir, mv):
    repo = tmp_path / "r"
    _repo(repo, f"name: p-ok\n{mv}version: 0.1.0\nkind: model-provider\n"
                "api_version: 1\nrequires_plugins: []\ntags: [a]\n")
    target, manifest, name = pc._install_plugin_core(f"file://{repo}", force=False)
    assert name == "p-ok" and (plugins_dir / "p-ok" / "plugin.yaml").exists()


def test_v2_fields_are_parsed_by_loader(tmp_path):
    from clover_cli.plugins import _parse_manifest_v2_fields
    out = _parse_manifest_v2_fields(
        {"manifest_version": 2, "api_version": 1, "tags": ["x"],
         "python_dependencies": ["requests>=2"], "license": "MIT",
         "requires_plugins": ["other"]}, "p")
    assert out["manifest_version"] == 2 and out["api_version"] == 1
    assert out["python_dependencies"] == ["requests>=2"] and out["tags"] == ["x"]


def test_v3_rejected_with_clear_message(tmp_path, plugins_dir):
    repo = tmp_path / "r"
    _repo(repo, "name: p-future\nmanifest_version: 99\n")
    with pytest.raises(pc.PluginOperationError) as e:
        pc._install_plugin_core(f"file://{repo}", force=False)
    msg = str(e.value)
    assert "requires manifest_version 99" in msg and "supports up to 2" in msg
    assert not (plugins_dir / "p-future").exists()


def test_non_integer_rejected(tmp_path, plugins_dir):
    repo = tmp_path / "r"
    _repo(repo, "name: p-bad\nmanifest_version: abc\n")
    with pytest.raises(pc.PluginOperationError, match="invalid manifest_version"):
        pc._install_plugin_core(f"file://{repo}", force=False)
