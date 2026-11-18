"""The system prompt + ``tools[]`` a stock install sends must not grow unexpectedly.

Both ride on every API call. ``tests/fixtures/prompt_surface/surfaces.json`` records their token
sizes per surface; a change that grows any of them past the allowance, or adds/removes a tool,
fails here until the fixture is regenerated in the same PR (a reviewable number):

    python scripts/ci/prompt_surface.py render
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("prompt_surface", REPO / "scripts" / "ci" / "prompt_surface.py")
assert _spec is not None and _spec.loader is not None
prompt_surface = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prompt_surface)

# Growth allowed over the committed fixture before the test fails: noise from dynamic text
# (dates, paths) stays under this, a new tool or a pasted paragraph does not.
ALLOWED_GROWTH_TOKENS = 150
ALLOWED_GROWTH_RATIO = 0.02


def _over(base: int, now: int) -> bool:
    return now - base > max(ALLOWED_GROWTH_TOKENS, int(base * ALLOWED_GROWTH_RATIO))


@pytest.fixture(scope="module")
def measured():
    return prompt_surface.measure()


def test_fixture_covers_every_surface():
    committed = json.loads(prompt_surface.FIXTURE.read_text(encoding="utf-8"))
    assert set(committed) == {name for name, _ in prompt_surface.SURFACES}


def test_prompt_surface_has_not_grown(measured):
    committed = json.loads(prompt_surface.FIXTURE.read_text(encoding="utf-8"))
    problems: list[str] = []
    for surface, base in committed.items():
        now = measured[surface]
        if _over(base["tool_tokens"], now["tool_tokens"]):
            problems.append(f"{surface}: tools[] {base['tool_tokens']} -> {now['tool_tokens']} tokens")
        for tier, tokens in base["prompt_tokens"].items():
            if _over(tokens, now["prompt_tokens"].get(tier, 0)):
                problems.append(f"{surface}: system prompt '{tier}' {tokens} -> {now['prompt_tokens'][tier]} tokens")
        added, removed = set(now["tools"]) - set(base["tools"]), set(base["tools"]) - set(now["tools"])
        if added or removed:
            problems.append(f"{surface}: tools added={sorted(added)} removed={sorted(removed)}")
    assert not problems, (
        "The prompt surface changed (sent on every API call). If intended, regenerate the fixture "
        "and justify the growth in the PR:\n    python scripts/ci/prompt_surface.py render\n"
        + "\n".join(problems)
    )


def test_stable_prompt_prefix_is_byte_stable_within_a_conversation(measured):
    """Prompt caching is sacred: building the prompt twice for one session yields identical bytes."""
    unstable = [s for s, m in measured.items() if not m["stable_prefix_is_byte_stable"]]
    assert not unstable, f"stable system prompt tier differs between builds on: {unstable}"


def test_growth_check_trips_on_a_pasted_paragraph():
    assert _over(1000, 1000 + ALLOWED_GROWTH_TOKENS + 1)
    assert not _over(1000, 1000 + ALLOWED_GROWTH_TOKENS)
    assert not _over(1000, 900)
