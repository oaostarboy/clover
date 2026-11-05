"""Fail-closed acceptance policy for in-flight native drivers."""


def require_first_update_progress(old, current):
    assert current != old, 'first update did not advance the source; manual retry cannot be an automatic PASS'


def require_guard_status(actual, expected):
    assert expected in {'legacy-proceed', 'capable'} and actual == expected, (actual, expected)
