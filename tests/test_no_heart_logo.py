"""The flat four-heart mark is NOT the Clover logo; never reference it."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_heart_mark_svg_not_referenced():
    assert not (ROOT / "assets" / "clover-mark.svg").exists()
    bad = []
    for d in ("clover_cli", "scripts", "packaging"):
        for p in (ROOT / d).rglob("*"):
            if p.is_file() and p.suffix in (".py", ".sh", ".ps1", ".nsi", ".md", ".yml", ".desktop", ""):
                if "clover-mark.svg" in p.read_text(errors="ignore"):
                    bad.append(str(p.relative_to(ROOT)))
    assert not bad, bad
