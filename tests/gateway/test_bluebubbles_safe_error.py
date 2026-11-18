import httpx

from gateway.platforms.bluebubbles import _safe_error


def test_safe_error_masks_password_query():
    exc = httpx.HTTPError("Client error 401 for url 'http://h:1234/api/v1/ping?password=hunter2secret'")
    out = _safe_error(exc)
    assert "hunter2secret" not in out and "/api/v1/ping" in out


def test_safe_error_empty_falls_back_to_type_name():
    assert _safe_error(TimeoutError()) == "TimeoutError"
