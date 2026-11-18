"""2.1 token savings: prune-on gates, archive pointers, cache TTL auto, config migration."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from agent.context_compressor import ContextCompressor
from agent.prompt_caching import (
    AUTO_CACHE_TTL,
    auto_cache_ttl_for_source,
    provider_cache_expired,
)
from agent.turn_proactive_prune import prune_stale_prefix
from tools.tool_result_storage import (
    PRUNED_SUBDIR,
    has_archive_pointer,
    write_pruned_original,
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path))
    monkeypatch.delenv("TERMINAL_ENV", raising=False)
    return tmp_path


# ── 3. cache TTL auto ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("source,expected", [
    ("cli", "1h"), ("telegram", "1h"), ("discord", "1h"), ("desktop", "1h"), (None, "1h"),
    ("api_server", "5m"), ("cron", "5m"), ("curator", "5m"), ("subagent", "5m"),
    ("webhook", "5m"), ("kanban", "5m"),
])
def test_auto_ttl_by_source(source, expected):
    assert auto_cache_ttl_for_source(source) == expected


def test_shipped_default_is_auto():
    from clover_cli.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["prompt_caching"]["cache_ttl"] == AUTO_CACHE_TTL
    template = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "cli-config.yaml.example").read_text(encoding="utf-8")
    )
    assert template["prompt_caching"]["cache_ttl"] == AUTO_CACHE_TTL


@pytest.mark.parametrize("platform,expected", [
    ("cli", "1h"), ("telegram", "1h"), ("cron", "5m"), ("subagent", "5m"), ("api_server", "5m"),
])
def test_unconfigured_agent_resolves_tier_from_platform(home, platform, expected):
    from run_agent import AIAgent

    (home / "config.yaml").write_text("model:\n  default: anthropic/claude-sonnet-4.6\n", encoding="utf-8")
    agent = AIAgent(
        session_id=f"ttl-{platform}", platform=platform, api_key="k",
        base_url="https://openrouter.ai/api/v1", provider="openrouter", api_mode="chat_completions",
        model="anthropic/claude-sonnet-4.6", quiet_mode=True, skip_context_files=True,
        skip_memory=True, save_trajectories=False, enabled_toolsets=["file"],
    )
    assert agent._cache_ttl == expected
    assert agent._use_prompt_caching and not agent._cache_disabled


def test_explicit_pin_beats_auto(home):
    from run_agent import AIAgent

    (home / "config.yaml").write_text(
        "_config_version: 43\nmodel:\n  default: anthropic/claude-sonnet-4.6\nprompt_caching:\n  cache_ttl: 5m\n", encoding="utf-8")
    agent = AIAgent(
        session_id="ttl-pin", platform="cli", api_key="k", base_url="https://openrouter.ai/api/v1",
        provider="openrouter", api_mode="chat_completions", model="anthropic/claude-sonnet-4.6",
        quiet_mode=True, skip_context_files=True, skip_memory=True, save_trajectories=False,
        enabled_toolsets=["file"],
    )
    assert agent._cache_ttl == "5m"


# ── config migration 43 ────────────────────────────────────────────────────────

def _migrate(home, cfg):
    from clover_cli.config_migrations import run_migrations

    (home / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    run_migrations(42, {"env_added": [], "config_added": [], "warnings": []}, quiet=True)
    return yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))


def test_migration_moves_old_defaults_to_new(home):
    from clover_cli.config import load_config

    out = _migrate(home, {
        "_config_version": 42,
        "compression": {"proactive_prune_tokens": 0, "proactive_prune_min_result_chars": 8000,
                        "proactive_prune_min_reclaim_tokens": 4096},
        "prompt_caching": {"cache_ttl": "5m"},
    })
    assert "proactive_prune_tokens" not in (out.get("compression") or {})
    assert "cache_ttl" not in (out.get("prompt_caching") or {})
    cfg = load_config()
    assert cfg["compression"]["proactive_prune_tokens"] == 64000
    assert cfg["compression"]["proactive_prune_min_result_chars"] == 1000
    assert cfg["compression"]["proactive_prune_min_reclaim_tokens"] == 64000
    assert cfg["prompt_caching"]["cache_ttl"] == "auto"


def test_migration_keeps_user_choices(home):
    out = _migrate(home, {
        "_config_version": 42,
        "compression": {"proactive_prune_tokens": 48000, "proactive_prune_min_reclaim_tokens": 8192},
        "prompt_caching": {"cache_ttl": "1h"},
    })
    assert out["compression"]["proactive_prune_tokens"] == 48000
    assert out["compression"]["proactive_prune_min_reclaim_tokens"] == 8192
    assert out["prompt_caching"]["cache_ttl"] == "1h"


# ── 1. prune gates ─────────────────────────────────────────────────────────────

def _agent(*, turns=3, caching=True, last_response_age=None, prune=None, session=True):
    comp = SimpleNamespace(
        prune_tool_results_only=prune, _session_db=object() if session else None, _session_id="s1")
    agent = SimpleNamespace(
        compression_enabled=True, context_compressor=comp, _user_turn_count=turns,
        _use_prompt_caching=caching, _cache_disabled=False, _cache_ttl="5m", model="m",
        provider="openrouter", session_id="s1",
        _last_api_response_at=None if last_response_age is None else time.time() - last_response_age,
    )
    return agent


def _history():
    return [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "old"},
        {"role": "tool", "content": "X" * 5000, "tool_call_id": "a"},
        {"role": "user", "content": "current turn"},
    ]


def _spy():
    calls = []

    def prune(messages, current_tokens=None, *, cold_cache=False):
        calls.append((len(messages), cold_cache))
        out = [dict(m) for m in messages]
        out[2]["content"] = "[pruned]"
        return out, 1
    return prune, calls


def test_cold_cache_prunes_prefix_only_and_keeps_running_turn():
    prune, calls = _spy()
    agent = _agent(last_response_age=4000, prune=prune)  # 5m tier, ~67 min idle
    msgs = _history()
    out = prune_stale_prefix(agent, msgs, 3, 100_000)
    assert out is not msgs and len(out) == len(msgs)
    assert out[2]["content"] == "[pruned]"
    assert out[3] is msgs[3]  # the running turn's user row is untouched
    assert calls == [(3, True)]  # prefix only (rows before the current user message)


def test_warm_cache_never_pruned():
    prune, calls = _spy()
    agent = _agent(last_response_age=10, prune=prune)
    msgs = _history()
    assert prune_stale_prefix(agent, msgs, 3, 100_000) is msgs
    assert calls == []


def test_first_turn_never_pruned():
    prune, calls = _spy()
    agent = _agent(turns=1, last_response_age=4000, prune=prune)
    msgs = _history()
    assert prune_stale_prefix(agent, msgs, 3, 100_000) is msgs and calls == []


def test_unknown_cache_state_is_treated_warm():
    prune, calls = _spy()
    assert prune_stale_prefix(_agent(last_response_age=None, prune=prune), _history(), 3, 1) is not None
    assert calls == []
    assert provider_cache_expired(_agent(caching=False, last_response_age=99999)) is False


def test_no_session_row_means_no_prune():
    prune, calls = _spy()
    agent = _agent(last_response_age=4000, prune=prune, session=False)
    msgs = _history()
    assert prune_stale_prefix(agent, msgs, 3, 1) is msgs and calls == []


def test_one_hour_tier_expires_later():
    agent = _agent(last_response_age=1000)
    agent._cache_ttl = "1h"
    assert provider_cache_expired(agent) is False  # 16 min < 1h
    agent._cache_ttl = "5m"
    assert provider_cache_expired(agent) is True


def test_engine_without_cold_cache_kwarg_is_skipped():
    def old_engine(messages, current_tokens=None):
        raise AssertionError("must not be called")

    agent = _agent(last_response_age=4000, prune=old_engine)
    msgs = _history()
    assert prune_stale_prefix(agent, msgs, 3, 1) is msgs


# ── 2. archive pointers ────────────────────────────────────────────────────────

def test_write_pruned_original_roundtrip(home):
    body = json.dumps({"k": "v" * 3000})
    host, display = write_pruned_original(body, "sess", "terminal", "call_1")
    assert Path(host).read_text(encoding="utf-8") == body
    assert f"{PRUNED_SUBDIR}/" in display and display.endswith(".json")
    second, _ = write_pruned_original(body, "sess", "terminal", "call_1")
    assert second != host  # never overwrites another original


def test_non_local_backend_writes_nothing(home, monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    assert write_pruned_original("x" * 5000, "s", "terminal", "c") is None
    assert not (home / PRUNED_SUBDIR).exists()


def test_has_archive_pointer():
    assert has_archive_pointer("[terminal] ran `ls` — full result: ~/.clover/cache/pruned/a_b.txt")
    assert not has_archive_pointer("plain result")
    assert not has_archive_pointer("X" * 3000 + " — full result: cache/pruned/a.txt")


def _call(cid):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": "terminal", "arguments": '{"command":"ls"}'}}]}


def test_proactive_prune_archives_originals_and_stub_names_file(home):
    from clover_state import SessionDB

    db = SessionDB(db_path=home / "state.db")
    db.create_session("S1", source="cli")
    big = [f"line {i} " + "z" * 80 for i in range(400)]
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    originals = {}
    for i in range(8):
        content = "\n".join(big) + str(i)
        originals[f"c{i}"] = content
        msgs += [_call(f"c{i}"), {"role": "tool", "tool_call_id": f"c{i}", "content": content}]
    msgs.append({"role": "assistant", "content": "ok"})
    db.append_messages_batch("S1", msgs)
    with patch("agent.context_compressor.get_model_context_length", return_value=1_000_000):
        comp = ContextCompressor(
            model="t", quiet_mode=True, protect_first_n=2, protect_last_n=4,
            proactive_prune_tokens=1000, proactive_prune_min_reclaim_tokens=1000)
    comp._session_db, comp._session_id = db, "S1"
    result, n = comp.prune_tool_results_only(db.get_messages_as_conversation("S1"), current_tokens=500_000)
    assert n >= 1
    stubs = [m for m in result if m.get("role") == "tool" and has_archive_pointer(m["content"])]
    assert stubs, "pruned stubs must name their archived original"
    for stub in stubs:
        name = stub["content"].rsplit("cache/pruned/", 1)[1]
        archived = (home / PRUNED_SUBDIR / name).read_text(encoding="utf-8")
        assert archived == originals[stub["tool_call_id"]]


def test_failed_archive_keeps_result_whole(home, monkeypatch):
    from agent.context_compressor_prune_archive import archive_pruned_originals

    msgs = [_call("c1"), {"role": "tool", "tool_call_id": "c1", "content": "A" * 5000}]
    pruned = [msgs[0], {"role": "tool", "tool_call_id": "c1", "content": "[terminal] summary"}]
    monkeypatch.setattr("agent.context_compressor_prune_archive.write_pruned_original", lambda *a, **k: None)
    rows, written, reverted = archive_pruned_originals(msgs, pruned, "s")
    assert reverted == 1 and written == [] and rows[1]["content"] == "A" * 5000


def test_compaction_drops_are_archived_for_summarizer(home):
    from agent.context_compressor_prune_archive import archive_compaction_drops

    msgs = [_call("c1"), {"role": "tool", "tool_call_id": "c1", "content": "B" * 5000}]
    pruned = [msgs[0], {"role": "tool", "tool_call_id": "c1", "content": "[terminal] ran `ls` (5,000 chars)"}]
    rows, written, reverted = archive_compaction_drops(msgs, pruned, "sess")
    assert reverted == 0 and len(written) == 1
    assert has_archive_pointer(rows[1]["content"])
    assert Path(written[0]).read_text(encoding="utf-8") == "B" * 5000


def test_pruned_archives_outlive_spillover_sweep(home):
    from tools.tool_result_storage import cleanup_spillover_cache

    host, _ = write_pruned_original("x" * 100, "s", "t", "c")
    old = time.time() - 3 * 86400
    os.utime(host, (old, old))
    cleanup_spillover_cache()
    assert Path(host).exists()  # 3 days old: still inside the 30-day pointer retention
    older = time.time() - 40 * 86400
    os.utime(host, (older, older))
    cleanup_spillover_cache()
    assert not Path(host).exists()


def test_system_prompt_byte_identical_across_turns_with_prune_enabled():
    """Prompt caching is sacred: a cold-cache prune rewrites old tool rows, never the system row."""
    prune, calls = _spy()
    agent = _agent(last_response_age=4000, prune=prune)
    system = {"role": "system", "content": "You are Clover.\nstable prefix \u2603 " + "p" * 3000}
    msgs = [system] + _history()[1:]
    before = json.dumps(msgs[0], sort_keys=True).encode()
    turn1 = prune_stale_prefix(agent, msgs, 3, 100_000)
    turn2 = prune_stale_prefix(agent, turn1, 3, 100_000)
    assert len(calls) == 2, "prune must run on both turns for this test to mean anything"
    assert turn1 is not msgs and turn1[2]["content"] == "[pruned]"
    assert json.dumps(turn1[0], sort_keys=True).encode() == before
    assert json.dumps(turn2[0], sort_keys=True).encode() == before
