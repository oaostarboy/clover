#!/usr/bin/env python3
"""Measure the model-facing prompt surface: system prompt tiers + ``tools[]`` token size per surface.

Every tool schema and every system-prompt byte is sent on every API call, so growth there is paid
by every user on every turn. This renders both through the production code (stock config, temp
CLOVER_HOME, clock pinned, network off) and records the sizes in
``tests/fixtures/prompt_surface/surfaces.json``. ``tests/ci/test_prompt_surface.py`` fails when a
surface grows past the allowance or gains/loses a tool without the fixture being regenerated
(``python scripts/ci/prompt_surface.py render``), so a one-line change that bloats the prompt shows
up in review as a number.

    python scripts/ci/prompt_surface.py render            # rewrite the committed fixture
    python scripts/ci/prompt_surface.py measure           # print the current numbers
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "prompt_surface" / "surfaces.json"
CHARS_PER_TOKEN = 4  # same rough estimate as agent.model_metadata.CHARS_PER_TOKEN
MODEL = "anthropic/claude-sonnet-4.5"
# (surface, platform): the matrix of stock surfaces whose toolsets / hints differ.
SURFACES = (("cli", "cli"), ("telegram", "telegram"), ("cron", "cron"), ("api_server", "api_server"))


def _child() -> int:
    """Runs in a clean process: build one agent per surface, print the measurements as JSON."""
    sys.path.insert(0, str(ROOT))
    import socket

    def refuse(*_a, **_k):
        raise OSError("network disabled while measuring the prompt surface")

    socket.socket.connect = refuse  # type: ignore[method-assign]
    socket.create_connection = refuse  # type: ignore[assignment]
    socket.getaddrinfo = refuse  # type: ignore[assignment]

    from agent.system_prompt import build_system_prompt_parts
    from clover_cli.config import load_config
    from clover_cli.tools_config import _get_platform_tools
    from run_agent import AIAgent

    result: dict[str, dict] = {}
    for surface, platform in SURFACES:
        toolsets = sorted(_get_platform_tools(load_config(), platform))
        agent = AIAgent(
            model=MODEL, api_key="inspect-only", base_url="https://openrouter.ai/api/v1",
            quiet_mode=True, save_trajectories=False, platform=platform, skip_context_files=True,
            skip_memory=True, session_id="20260115_120000_prompt_surface", enabled_toolsets=toolsets,
        )
        first = build_system_prompt_parts(agent)
        second = build_system_prompt_parts(agent)
        tools = {t["function"]["name"]: t["function"] for t in (agent.tools or [])}
        result[surface] = {
            "prompt_tokens": {tier: len(text) // CHARS_PER_TOKEN for tier, text in first.items()},
            "stable_prefix_is_byte_stable": first.get("stable") == second.get("stable"),
            "tool_tokens": len(json.dumps(tools, sort_keys=True)) // CHARS_PER_TOKEN,
            "tools": sorted(tools),
        }
    print("PROMPT_SURFACE_JSON=" + json.dumps(result, sort_keys=True))
    return 0


def measure() -> dict[str, dict]:
    """Measure every surface from a fresh child process and throwaway home."""
    with tempfile.TemporaryDirectory(prefix="prompt-surface-") as tmp:
        home = Path(tmp) / "home"
        (home / ".clover").mkdir(parents=True)
        env = {
            "PATH": os.environ.get("PATH", ""), "HOME": str(home), "CLOVER_HOME": str(home / ".clover"),
            "TZ": "UTC", "LANG": "C.UTF-8", "PYTHONHASHSEED": "0", "PYTHONPATH": str(ROOT),
            "TMPDIR": tmp, "PYTHONDONTWRITEBYTECODE": "1",
        }
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "_child"], env=env, cwd=tmp,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=600,
            check=False,
        )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("PROMPT_SURFACE_JSON=")]
    if proc.returncode != 0 or not lines:
        raise RuntimeError(f"prompt surface child failed ({proc.returncode}):\n{proc.stderr[-4000:]}")
    return json.loads(lines[-1].split("=", 1)[1])


def render() -> int:
    data = measure()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "measure"
    if cmd == "_child":
        raise SystemExit(_child())
    if cmd == "render":
        raise SystemExit(render())
    print(json.dumps(measure(), indent=2, sort_keys=True))
