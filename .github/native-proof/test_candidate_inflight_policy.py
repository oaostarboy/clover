"""Fail-closed checks for candidate in-flight native driver outcomes."""
import pytest

from candidate_inflight_policy import require_first_update_progress, require_guard_status


def test_unchanged_source_cannot_be_retried_into_pass():
    with pytest.raises(AssertionError, match="first update"):
        require_first_update_progress("old", "old")


def test_changed_source_can_proceed_to_full_successor_checks():
    require_first_update_progress("old", "target")


def test_capable_and_legacy_guards_must_match_original_base():
    require_guard_status("legacy-proceed", "legacy-proceed")
    require_guard_status("capable", "capable")
    with pytest.raises(AssertionError):
        require_guard_status("legacy-proceed", "capable")
    with pytest.raises(AssertionError):
        require_guard_status("refused", "legacy-proceed")
