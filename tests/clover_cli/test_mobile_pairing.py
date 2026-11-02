from clover_cli.mobile_pairing import pairing_info, strict_https_origin

KEY = "fake-test-key-123456"


def test_https_origin_only():
    assert strict_https_origin("https://gateway.example.com/") == "https://gateway.example.com"
    assert strict_https_origin("https://gateway.example.com:8443") == "https://gateway.example.com:8443"
    for bad in ["http://gateway.example.com", "https://u:p@x.com", "https://x.com/a", "https://x.com?q=1", "", None, "192.168.1.5"]:
        assert strict_https_origin(bad) is None


def test_not_set_up_without_https_url():
    assert pairing_info({}, KEY)["configured"] is False
    assert pairing_info({"mobile": {"public_url": "http://192.168.1.2:8642"}}, KEY)["token"] is None


def test_configured():
    info = pairing_info({"mobile": {"public_url": "https://gateway.example.com"}}, KEY)
    assert info["configured"] and info["origin"] == "https://gateway.example.com" and info["token"] == KEY


def test_missing_key():
    info = pairing_info({"mobile": {"public_url": "https://gateway.example.com"}}, "")
    assert not info["configured"] and info["reason"] == "no_gateway_key" and info["token"] is None
