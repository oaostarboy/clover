"""The desktop build fingerprint must cover every input the bundle is built from."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pathspec")

from clover_cli import main as m


def _make_repo(root: Path) -> Path:
    (root / ".gitignore").write_text("node_modules/\ndist/\nrelease/\n__pycache__/\n*.pyc\n")
    (root / "package.json").write_text("{}")
    d = root / "apps" / "desktop"
    (d / "electron").mkdir(parents=True)
    (d / "electron" / "main.ts").write_text(
        "import { x } from '../../../services/managed-relay/src/desktop-pairing.mjs'\n"
    )
    (d / "vite.config.ts").write_text("alias: { '@shared': path.resolve(__dirname, '../shared/src') }\n")
    relay = root / "services" / "managed-relay" / "src"
    relay.mkdir(parents=True)
    (relay / "desktop-pairing.mjs").write_text("import { open } from './desktop-connector.mjs'\n")
    (relay / "desktop-connector.mjs").write_text("export const open = 1\n")
    (relay / "unrelated.mjs").write_text("export const u = 1\n")
    shared = root / "apps" / "shared" / "src"
    shared.mkdir(parents=True)
    (shared / "index.ts").write_text("export {}\n")
    (root / "agent").mkdir()
    (root / "agent" / "thing.py").write_text("x = 1\n")
    return root


def test_vendored_connector_change_changes_fingerprint(tmp_path):
    root = _make_repo(tmp_path)
    before = m._compute_desktop_content_hash(root)
    # Imported transitively via desktop-pairing.mjs -> desktop-connector.mjs
    (root / "services/managed-relay/src/desktop-connector.mjs").write_text("export const open = 2\n")
    assert m._compute_desktop_content_hash(root) != before


def test_aliased_shared_source_change_changes_fingerprint(tmp_path):
    root = _make_repo(tmp_path)
    before = m._compute_desktop_content_hash(root)
    (root / "apps/shared/src/index.ts").write_text("export const y = 1\n")
    assert m._compute_desktop_content_hash(root) != before


def test_unrelated_python_change_keeps_fingerprint(tmp_path):
    root = _make_repo(tmp_path)
    before = m._compute_desktop_content_hash(root)
    (root / "agent" / "thing.py").write_text("x = 2\n")
    (root / "services/managed-relay/src/unrelated.mjs").write_text("export const u = 2\n")
    (root / "apps/desktop/node_modules").mkdir()
    (root / "apps/desktop/node_modules/junk.js").write_text("1")
    assert m._compute_desktop_content_hash(root) == before


def _built(root: Path, monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CLOVER_HOME", str(home))
    exe = root / "apps/desktop/dist"
    exe.mkdir(parents=True, exist_ok=True)
    (exe / "index.html").write_text("<html></html>")
    monkeypatch.setattr(m, "_desktop_packaged_executable", lambda _d: Path(__file__))
    monkeypatch.setattr(m, "_renderer_bundle_torn", lambda _d: False)
    return home


def test_build_needed_after_connector_change_and_stale_stamp(tmp_path, monkeypatch):
    (tmp_path / "repo").mkdir()
    root = _make_repo(tmp_path / "repo")
    _built(root, monkeypatch, tmp_path)
    desktop = root / "apps" / "desktop"
    m._write_desktop_build_stamp(root, source_mode=False)
    assert m._desktop_build_needed(desktop, root, source_mode=False) is False

    # A release that only touches the vendored connector must trigger a rebuild.
    (root / "services/managed-relay/src/desktop-connector.mjs").write_text("export const open = 3\n")
    assert m._desktop_build_needed(desktop, root, source_mode=False) is True

    # Rebuilding refreshes the stamp.
    m._write_desktop_build_stamp(root, source_mode=False)
    assert m._desktop_build_needed(desktop, root, source_mode=False) is False

    # Stale stamp (old schema without a hash) and a missing stamp both rebuild.
    stamp = m._desktop_stamp_path()
    stamp.write_text(json.dumps({"sourceMode": False}))
    assert m._desktop_build_needed(desktop, root, source_mode=False) is True
    stamp.unlink()
    assert m._desktop_build_needed(desktop, root, source_mode=False) is True
