"""Phone Library tab: artifact index, pagination, thumbnails, thumb route auth + traversal."""

from __future__ import annotations

import os
import shutil
import types

import pytest

from tui_gateway import artifact_index as ai


def _png(path, size=(64, 48), color=(10, 200, 120)):
    from PIL import Image

    Image.new("RGB", size, color).save(path)


@pytest.fixture()
def files(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    out = {}
    for name in ("a.png", "b.jpg"):
        _png(d / name)
        out[name] = str(d / name)
    for name, data in (("c.mp4", b"x" * 10), ("v.mp3", b"id3"), ("doc.pdf", b"%PDF"), ("n.md", b"# hi"),
                       ("page.html", b"<p>x</p>"), ("s.py", b"print(1)"), ("blob.bin", b"\0")):
        (d / name).write_bytes(data)
        out[name] = str(d / name)
    return out


def test_kind_detection():
    assert ai.kind_of("/x/a.PNG") == "image"
    assert ai.kind_of("/x/a.mov") == "video"
    assert ai.kind_of("/x/a.m4a") == "audio"
    assert ai.kind_of("/x/a.pdf") == "doc"
    assert ai.kind_of("/x/a.md") == "doc"
    assert ai.kind_of("/x/a.html") == "html"
    assert ai.kind_of("/x/a.tsx") == "code"
    assert ai.kind_of("/x/a.bin") == "file"
    assert ai.kind_of("/x/noext") == "file"


def test_extract_refs_media_and_markdown():
    text = (
        "Here you go\nMEDIA:/tmp/a.png\n![chart](/tmp/chart.webp)\n[spec](/tmp/spec.pdf)\n"
        "[web](https://example.com/x.png)\n[rel](notes.md)\n`MEDIA:\"/tmp/with space.mp3\"`\n[dup](/tmp/a.png)"
    )
    refs = dict(ai.extract_refs(text))
    assert set(refs) == {"/tmp/a.png", "/tmp/chart.webp", "/tmp/spec.pdf", "/tmp/with space.mp3"}
    assert refs["/tmp/chart.webp"] == "chart"


def test_extract_refs_ignores_unknown_ext_in_markdown_but_keeps_media_tag():
    refs = dict(ai.extract_refs("[x](/tmp/data.zzz)\nMEDIA:/tmp/data.zzz"))
    assert list(refs) == ["/tmp/data.zzz"]


def test_build_skips_missing_sensitive_and_dirs(files, tmp_path):
    (tmp_path / ".env").write_text("K=1")
    rows = [
        {"id": 1, "session_id": "s1", "timestamp": 100.0, "content": f"MEDIA:{files['a.png']}\nMEDIA:/nope/missing.png"},
        {"id": 2, "session_id": "s1", "timestamp": 101.0, "content": f"MEDIA:{tmp_path}/.env\nMEDIA:{tmp_path}"},
    ]
    items = ai.build_artifacts(rows)
    assert [i["path"] for i in items] == [files["a.png"]]
    it = items[0]
    assert it["kind"] == "image" and it["mime"] == "image/png" and it["size"] > 0
    assert it["session_id"] == "s1" and it["message_id"] == 1
    assert it["thumb_url"] == f"/api/artifacts/thumb?id={it['id']}"


def test_newest_mention_wins_and_order(files):
    rows = [
        {"id": 1, "session_id": "old", "timestamp": 10.0, "content": f"MEDIA:{files['a.png']}"},
        {"id": 5, "session_id": "new", "timestamp": 50.0, "content": f"MEDIA:{files['a.png']}\nMEDIA:{files['c.mp4']}"},
        {"id": 3, "session_id": "mid", "timestamp": 30.0, "content": [{"type": "text", "text": f"MEDIA:{files['v.mp3']}"}]},
    ]
    items = ai.build_artifacts(rows)
    assert [i["kind"] for i in items] == ["image", "video", "audio"] or [i["created_at"] for i in items] == sorted(
        [i["created_at"] for i in items], reverse=True)
    by_path = {i["path"]: i for i in items}
    assert by_path[files["a.png"]]["session_id"] == "new"
    assert by_path[files["a.png"]]["message_id"] == 5
    assert [i["created_at"] for i in items] == sorted((i["created_at"] for i in items), reverse=True)
    assert by_path[files["c.mp4"]]["thumb_url"] and by_path[files["v.mp3"]]["thumb_url"] is None


def _many(files, n=7):
    rows = []
    names = ["a.png", "b.jpg", "c.mp4", "v.mp3", "doc.pdf", "n.md", "page.html", "s.py"]
    for i, name in enumerate(names[:n]):
        rows.append({"id": i + 1, "session_id": "s", "timestamp": float(100 + i), "content": f"MEDIA:{files[name]}"})
    return ai.build_artifacts(rows)


def test_pagination_walks_everything_once(files):
    items = _many(files, 8)
    seen, cursor = [], None
    for _ in range(10):
        pg = ai.page(items, cursor=cursor, limit=3)
        seen += [i["id"] for i in pg["items"]]
        cursor = pg["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 8
    assert seen == [i["id"] for i in items]


def test_filter_kinds_and_counts(files):
    items = _many(files, 8)
    assert {i["kind"] for i in ai.page(items, kind="images")["items"]} == {"image"}
    assert len(ai.page(items, kind="images")["items"]) == 2
    assert [i["kind"] for i in ai.page(items, kind="video")["items"]] == ["video"]
    assert {i["kind"] for i in ai.page(items, kind="docs")["items"]} == {"doc", "html"}
    assert [i["kind"] for i in ai.page(items, kind="code")["items"]] == ["code"]
    pg = ai.page(items, kind="audio")
    assert pg["counts"]["all"] == 8 and pg["counts"]["image"] == 2 and pg["total"] == 1


def test_page_limit_clamped_and_bad_cursor_ignored(files):
    items = _many(files, 8)
    assert len(ai.page(items, limit=0)["items"]) >= 1
    assert len(ai.page(items, limit=10_000)["items"]) == 8
    assert len(ai.page(items, cursor="garbage")["items"]) == 8


def test_registry_lookup_only_serves_indexed_ids(files, tmp_path):
    home = tmp_path / "home"
    items = _many(files, 8)
    ai.save_registry(home, items)
    img = next(i for i in items if i["kind"] == "image")
    assert ai.lookup(home, img["id"]) == img["path"]
    assert ai.lookup(home, "0" * 16) is None
    assert ai.lookup(home, "../../etc/passwd") is None
    assert ai.lookup(home, "") is None
    audio = next(i for i in items if i["kind"] == "audio")
    assert ai.lookup(home, audio["id"]) is None  # not thumbnailable, never registered


def test_registry_rejects_tampered_entry(files, tmp_path):
    import json

    home = tmp_path / "home"
    ai.save_registry(home, _many(files, 2))
    reg = home / "cache" / "artifacts" / "registry.json"
    data = json.loads(reg.read_text())
    bad_id = "a" * 16
    data[bad_id] = "/etc/passwd"  # id does not hash to path
    reg.write_text(json.dumps(data))
    assert ai.lookup(home, bad_id) is None


def test_image_thumbnail_cached(files, tmp_path):
    home = tmp_path / "home"
    _png(files["a.png"], size=(300, 200))
    t1 = ai.thumbnail(home, files["a.png"], 64)
    assert t1 and t1.is_file() and t1.suffix == ".jpg"
    from PIL import Image

    assert max(Image.open(t1).size) == 64
    mtime = t1.stat().st_mtime_ns
    t2 = ai.thumbnail(home, files["a.png"], 64)
    assert t2 == t1 and t2.stat().st_mtime_ns == mtime
    assert str(t1).startswith(str(home))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_video_thumbnail(tmp_path):
    import subprocess

    mp4 = tmp_path / "t.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", str(mp4)], check=True)
    t = ai.thumbnail(tmp_path / "home", str(mp4), 64)
    assert t and t.is_file()


def test_thumbnail_none_for_non_media(files, tmp_path):
    assert ai.thumbnail(tmp_path / "h", files["doc.pdf"]) is None
    assert ai.thumbnail(tmp_path / "h", "/nope/missing.png") is None


# ---------------------------------------------------------------- route: auth + traversal

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from clover_cli import web_server as ws

    monkeypatch.setenv("CLOVER_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ws, "get_clover_home", lambda: tmp_path / "home")
    return TestClient(ws.app), ws


def test_thumb_route_requires_auth(client, files, tmp_path):
    c, ws = client
    items = _many(files, 8)
    ai.save_registry(tmp_path / "home", items)
    img = next(i for i in items if i["kind"] == "image")
    r = c.get(f"/api/artifacts/thumb?id={img['id']}")
    assert r.status_code in (401, 403)
    r = c.get(f"/api/artifacts/thumb?id={img['id']}&token={ws._SESSION_TOKEN}")
    assert r.status_code in (401, 403)  # no query-token fallback on this route


def test_thumb_route_serves_indexed_and_rejects_rest(client, files, tmp_path):
    c, ws = client
    items = _many(files, 8)
    ai.save_registry(tmp_path / "home", items)
    hdr = {"Authorization": f"Bearer {ws._SESSION_TOKEN}"}
    img = next(i for i in items if i["kind"] == "image")
    ok = c.get(f"/api/artifacts/thumb?id={img['id']}&size=64", headers=hdr)
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/jpeg"
    for bad in ("0" * 16, "..%2F..%2Fetc%2Fpasswd", "%2Fetc%2Fpasswd", "a" * 15, ""):
        assert c.get(f"/api/artifacts/thumb?id={bad}", headers=hdr).status_code in (404, 422), bad
    # a path is never accepted as a parameter
    assert c.get("/api/artifacts/thumb?path=/etc/passwd", headers=hdr).status_code == 422


# ---------------------------------------------------------------- sessions DB scan

def test_scan_rows_from_real_sessiondb(tmp_path, monkeypatch, files):
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    from clover_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("sess1", source="ios")
        db.append_message("sess1", "user", "make me a picture")
        db.append_message("sess1", "assistant", f"Done.\nMEDIA:{files['a.png']}")
        db.append_message("sess1", "assistant", "no files here")
        db.append_message("sess1", "assistant", f"see ![x]({files['n.md']})")
        rows = ai.scan_rows(db)
        assert len(rows) == 2
        items = ai.build_artifacts(rows)
        assert {i["kind"] for i in items} == {"image", "doc"}
        assert all(i["session_id"] == "sess1" and isinstance(i["message_id"], int) for i in items)
        pg = ai.page(items, kind="images")
        assert [i["path"] for i in pg["items"]] == [files["a.png"]]
    finally:
        db.close()


# ---------------------------------------------------------------- incremental persistent index

def _wait_idle(home, timeout=60):
    import time

    t0 = time.time()
    while ai.is_indexing(home) and time.time() - t0 < timeout:
        time.sleep(0.05)
    assert not ai.is_indexing(home)


@pytest.fixture(scope="module")
def _big_template(tmp_path_factory):
    """Built once: ~200k messages (every 1000th is an assistant message naming a file)."""
    import sqlite3

    root = tmp_path_factory.mktemp("bigtpl")
    work = root / "work"
    work.mkdir()
    paths = []
    for name in ("a.png", "b.jpg"):
        _png(work / name)
        paths.append(str(work / name))
    for name, data in (("doc.pdf", b"%PDF"), ("n.md", b"# hi")):
        (work / name).write_bytes(data)
        paths.append(str(work / name))
    from clover_state import SessionDB

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CLOVER_HOME", str(root / "home"))
        db = SessionDB(db_path=root / "state.db")
        db.create_session("s1", source="ios")
        db.close()
    conn = sqlite3.connect(root / "state.db")
    rows = []
    for i in range(200_000):
        if i % 1000 == 1:
            content = f"made it\nMEDIA:{paths[(i // 1000) % 4]}"
        else:
            content = "plain reply " + "x" * 40
        rows.append(("s1", "assistant" if i % 2 else "user", content, 1000.0 + i))
    conn.executemany("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return root / "state.db", paths


@pytest.fixture()
def big_db(tmp_path, monkeypatch, _big_template):
    tpl, paths = _big_template
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CLOVER_HOME", str(home))
    monkeypatch.setattr(ai, "_states", {})
    monkeypatch.setattr(ai, "_cache", {"at": 0.0, "key": None, "items": []})
    monkeypatch.setattr(ai, "_PAUSE", 0.0)
    shutil.copy(tpl, tmp_path / "state.db")
    from clover_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    db.template_paths = paths
    yield db, home
    db.close()


def test_first_call_does_not_block_and_backfill_completes(big_db):
    import time

    db, home = big_db
    t0 = time.perf_counter()
    items = ai.index_for(db, home, force=True)
    assert time.perf_counter() - t0 < 1.0          # nothing scanned inline for the history
    assert ai.is_indexing(home) or items            # backfill is (or just was) running in the background
    _wait_idle(home)
    items = ai.index_for(db, home, force=True)
    assert {i["path"] for i in items} == set(db.template_paths)
    assert (home / "cache" / "artifacts" / "index.json").is_file()


def test_incremental_call_is_fast_and_sees_new_messages(big_db, files):
    import time

    db, home = big_db
    ai.index_for(db, home, force=True)
    _wait_idle(home)
    ai.index_for(db, home, force=True)
    t0 = time.perf_counter()
    ai.index_for(db, home, force=True)
    assert time.perf_counter() - t0 < 0.2           # warm, nothing new
    db.append_message("s1", "assistant", f"fresh\nMEDIA:{files['s.py']}")
    t0 = time.perf_counter()
    items = ai.index_for(db, home, force=True)
    assert time.perf_counter() - t0 < 0.2           # only id > high is scanned
    assert files["s.py"] in {i["path"] for i in items}
    assert items[0]["path"] == files["s.py"]        # newest first


def test_index_survives_restart_without_rescan(big_db, monkeypatch):
    db, home = big_db
    ai.index_for(db, home, force=True)
    _wait_idle(home)
    before = [i["id"] for i in ai.index_for(db, home, force=True)]
    monkeypatch.setattr(ai, "_states", {})           # new process: state comes from index.json
    scans = []
    real = ai.scan_rows
    monkeypatch.setattr(ai, "scan_rows", lambda *a, **k: scans.append(k) or real(*a, **k))
    after = [i["id"] for i in ai.index_for(db, home, force=True)]
    assert after == before and not ai.is_indexing(home)
    assert scans == []                               # nothing new -> no scan at all


def test_pagination_during_and_after_backfill(big_db):
    db, home = big_db
    ai.index_for(db, home, force=True)
    _wait_idle(home)
    items = ai.index_for(db, home, force=True)
    seen, cur = [], None
    while True:
        pg = ai.page(items, cursor=cur, limit=1, include_uploads=True)
        seen += [i["id"] for i in pg["items"]]
        cur = pg["next_cursor"]
        if not cur:
            break
    assert seen == [i["id"] for i in items] and len(set(seen)) == len(seen) == 4


def test_deleted_file_drops_out_without_rescan(big_db, files):
    db, home = big_db
    ai.index_for(db, home, force=True)
    _wait_idle(home)
    db.append_message("s1", "assistant", f"MEDIA:{files['n.md']}")
    assert files["n.md"] in {i["path"] for i in ai.index_for(db, home, force=True)}
    os.remove(files["n.md"])
    assert files["n.md"] not in {i["path"] for i in ai.index_for(db, home, force=True)}


def test_long_gap_catches_up_in_background(big_db, files, monkeypatch):
    db, home = big_db
    ai.index_for(db, home, force=True)
    _wait_idle(home)
    monkeypatch.setattr(ai, "_CHUNK", 50)
    import sqlite3

    conn = sqlite3.connect(db.db_path)
    conn.executemany("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                     [("s1", "assistant", "pad", 5e6 + i) for i in range(200)]
                     + [("s1", "assistant", f"MEDIA:{files['page.html']}", 6e6)])
    conn.commit()
    conn.close()
    ai.index_for(db, home, force=True)
    _wait_idle(home)
    assert files["page.html"] in {i["path"] for i in ai.index_for(db, home, force=True)}
