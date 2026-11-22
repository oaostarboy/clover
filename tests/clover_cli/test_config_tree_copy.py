"""read_raw_config copy semantics: mutation isolation + reload."""
import datetime

from clover_cli import config as cfg


def test_copy_tree_independent_and_equal():
    src = {"a": [1, {"b": [2, 3]}], "c": {"d": None, "e": "s"}}
    out = cfg._copy_config_tree(src)
    assert out == src and out is not src
    out["a"][1]["b"].append(9)
    out["c"]["x"] = 1
    assert src == {"a": [1, {"b": [2, 3]}], "c": {"d": None, "e": "s"}}


def test_copy_tree_falls_back_for_non_plain_types():
    src = {"when": datetime.date(2026, 1, 1), "tags": {1, 2}, "l": [1]}
    out = cfg._copy_config_tree(src)
    assert out == src and out["tags"] is not src["tags"]


def test_read_raw_config_mutation_does_not_poison_cache_and_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path))
    p = tmp_path / "config.yaml"
    p.write_text("telegram:\n  reactions: true\nitems: [1, 2]\n")
    first = cfg.read_raw_config()
    first["items"].append(99)
    first["telegram"]["reactions"] = False
    second = cfg.read_raw_config()
    assert second["items"] == [1, 2] and second["telegram"]["reactions"] is True
    p.write_text("telegram:\n  reactions: false\nitems: [1, 2, 3, 4]\n")
    third = cfg.read_raw_config()
    assert third["items"] == [1, 2, 3, 4] and third["telegram"]["reactions"] is False


def test_read_raw_config_tenant_isolation(tmp_path, monkeypatch):
    for name, val in (("a", 1), ("b", 2)):
        d = tmp_path / name
        d.mkdir()
        (d / "config.yaml").write_text(f"v: {val}\n")
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path / "a"))
    ra = cfg.read_raw_config()
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path / "b"))
    rb = cfg.read_raw_config()
    assert (ra["v"], rb["v"]) == (1, 2)
