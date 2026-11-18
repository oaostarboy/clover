from unittest.mock import patch

from cron import monitor


def test_blocks_private_destination():
    with patch("tools.url_safety.is_safe_url", return_value=False):
        ok, msg = monitor._fetch_monitor_url("http://169.254.169.254/latest/meta-data")
    assert not ok and "blocked" in msg


def test_rejects_non_http():
    ok, msg = monitor._fetch_monitor_url("file:///etc/passwd")
    assert not ok and "http(s)" in msg


def test_redirect_to_private_blocked():
    import httpx

    def handler(req):
        return httpx.Response(302, headers={"location": "http://10.0.0.1/x"})

    def safe(u):
        return "10.0.0.1" not in u

    def fake_client(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    with patch("tools.url_safety.is_safe_url", side_effect=safe), \
         patch("tools.url_safety.create_ssrf_safe_client", side_effect=fake_client):
        ok, msg = monitor._fetch_monitor_url("http://example.com/")
    assert not ok and "blocked" in msg


def test_redirect_loop_capped_and_ua():
    import httpx
    seen = []

    def handler(req):
        seen.append(req.headers["user-agent"])
        return httpx.Response(302, headers={"location": "/again"})

    def fake_client(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    with patch("tools.url_safety.is_safe_url", return_value=True), \
         patch("tools.url_safety.create_ssrf_safe_client", side_effect=fake_client):
        ok, msg = monitor._fetch_monitor_url("http://example.com/")
    assert not ok and "redirects" in msg
    assert seen[0] == "clover-cron-monitor"


def test_ok_body():
    import httpx
    def fake_client(**kw):
        return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=httpx.ByteStream(b"hi"))), **kw)
    with patch("tools.url_safety.is_safe_url", return_value=True), \
         patch("tools.url_safety.create_ssrf_safe_client", side_effect=fake_client):
        assert monitor._fetch_monitor_url("http://example.com/") == (True, "hi")
