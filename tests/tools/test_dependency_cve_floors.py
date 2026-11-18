"""Pinned dependency floors for known-CVE bumps (2.1 py-deps)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = (ROOT / "pyproject.toml").read_text()
LAZY = (ROOT / "tools" / "lazy_deps.py").read_text()
LOCK = (ROOT / "uv.lock").read_text()


def _v(s):
    return tuple(int(x) for x in s.split("."))


def _lock_version(name):
    m = re.search(rf'\[\[package\]\]\nname = "{re.escape(name)}"\nversion = "([^"]+)"', LOCK)
    assert m, name
    return m.group(1)


FLOORS = {
    "certifi": "2026.10.19",
    "aiohttp": "3.14.5",
    "urllib3": "2.8.0",
    "tornado": "6.6",
    "multidict": "6.9.1",
    "mcp": "2.3.0",
    "opentelemetry-api": "1.45.2",
    "opentelemetry-sdk": "1.45.2",
}


def test_lock_meets_cve_floors():
    for name, floor in FLOORS.items():
        assert _v(_lock_version(name)) >= _v(floor), name


def test_no_stale_pins_in_manifests():
    for stale in ("aiohttp==3.14.3", "certifi==2026.5.20", "mcp==2.0.0",
                  "opentelemetry-sdk==1.39.1",
                  "opentelemetry-exporter-otlp-proto-http==1.39.1"):
        assert stale not in PYPROJECT, stale
        assert stale not in LAZY, stale
    assert "urllib3>=2.7.0" not in PYPROJECT
