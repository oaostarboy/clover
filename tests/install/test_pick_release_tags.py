from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True)


def test_release_picker_accepts_semver_and_date_release_tags(tmp_path: Path) -> None:
    repo = tmp_path / "release-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(
        repo,
        "-c", "user.name=Test",
        "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-qm", "base",
    )
    for tag in ("v1.1.4", "v2026.7.7"):
        _git(repo, "tag", tag)

    script = Path(__file__).resolve().parents[2] / "scripts/sandbox/pick-release-tags.sh"
    result = subprocess.run(
        ["bash", str(script), "--count", "2", "--repo", str(repo)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["v1.1.4", "v2026.7.7"]


def test_release_picker_excludes_tags_on_the_commit_under_test(tmp_path: Path) -> None:
    # Right after a release is cut, the newest tag points at the commit CI is
    # testing. "Update from X to X" is not an update; the picker must skip it.
    repo = tmp_path / "release-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    ident = ("-c", "user.name=Test", "-c", "user.email=test@example.invalid")
    _git(repo, *ident, "commit", "--allow-empty", "-qm", "old")
    _git(repo, "tag", "v1.0.0")
    _git(repo, *ident, "commit", "--allow-empty", "-qm", "head")
    _git(repo, "tag", "v1.1.0")

    script = Path(__file__).resolve().parents[2] / "scripts/sandbox/pick-release-tags.sh"

    def pick(*extra: str) -> list[str]:
        result = subprocess.run(
            ["bash", str(script), "--count", "5", "--repo", str(repo), *extra],
            check=False, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    assert pick() == ["v1.0.0", "v1.1.0"]
    assert pick("--exclude-commit", "HEAD") == ["v1.0.0"]

    _git(repo, "tag", "-d", "v1.0.0")
    assert pick("--exclude-commit", "HEAD") == []
