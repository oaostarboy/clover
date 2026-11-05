"""Fail-closed checks for candidate in-flight native driver outcomes."""
import pytest

from candidate_inflight_policy import require_first_update_progress, require_guard_status, desktop_build_processes, require_config_preserved


def test_unchanged_source_cannot_be_retried_into_pass():
    with pytest.raises(AssertionError, match="first update"):
        require_first_update_progress("old", "old")


def test_changed_source_can_proceed_to_full_successor_checks():
    require_first_update_progress("old", "target")


def test_capable_and_legacy_guards_must_match_original_base():
    require_guard_status("legacy-proceed", "legacy-proceed")
    require_guard_status("capable", "capable")
    require_guard_status(None, "capable", {"update_restart_capability": "reply-carriers-through-ack-v1", "gateway_state": "draining", "restart_requested": True, "active_agents": 1})
    with pytest.raises(AssertionError):
        require_guard_status(None, "capable", {"update_restart_capability": "reply-carriers-through-ack-v1"})
    with pytest.raises(AssertionError):
        require_guard_status("legacy-proceed", "capable")
    with pytest.raises(AssertionError):
        require_guard_status("refused", "legacy-proceed")


def test_desktop_build_wait_only_matches_disposable_install():
    processes = [
        {"pid": 1, "cmdline": ["C:/fixture/source/venv/Scripts/python.exe", "-m", "clover_cli.main", "desktop", "--build-only"]},
        {"pid": 2, "cmdline": ["C:/other/source/venv/Scripts/python.exe", "-m", "clover_cli.main", "desktop", "--build-only"]},
        {"pid": 3, "cmdline": ["C:/fixture/source/venv/Scripts/python.exe", "-m", "clover_cli.main", "gateway", "run"]},
    ]
    assert desktop_build_processes(processes, "C:/fixture/source") == [1]


def test_original_config_values_survive_new_defaults():
    before = {'terminal': {'cwd': '/fixture', 'backend': 'local'}, 'streaming': {'enabled': False}}
    require_config_preserved(before, {'terminal': {'cwd': '/fixture', 'backend': 'local', 'new': 1}, 'streaming': {'enabled': False}, 'new': 2})
    with pytest.raises(AssertionError):
        require_config_preserved(before, {'terminal': {'cwd': '/other', 'backend': 'local'}, 'streaming': {'enabled': False}})
    with pytest.raises(AssertionError):
        require_config_preserved(before, {'terminal': {'cwd': '/fixture'}, 'streaming': {'enabled': False}})


def test_config_schema_version_may_advance_but_never_regress():
    require_config_preserved({'_config_version': 41, 'terminal': {'backend': 'local'}}, {'_config_version': 42, 'terminal': {'backend': 'local'}})
    with pytest.raises(AssertionError):
        require_config_preserved({'_config_version': 41}, {'_config_version': 40})
