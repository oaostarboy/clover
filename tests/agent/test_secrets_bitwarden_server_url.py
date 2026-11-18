import pytest

from agent.secret_sources import bitwarden as bw


@pytest.mark.parametrize("url", ["", "https://vault.bitwarden.eu", "http://localhost:8087", "http://127.0.0.1/"])
def test_valid(url):
    assert bw.validate_server_url(url) == url


@pytest.mark.parametrize("url", ["ftp://x.com", "https://", "https://u:p@vault.example", "http://attacker.example", "javascript:alert(1)"])
def test_invalid(url):
    with pytest.raises(ValueError):
        bw.validate_server_url(url)


def test_apply_drops_bad_and_inherited():
    env = {}
    assert "Ignoring" in bw.apply_server_url_to_env(env, "http://evil.example")
    assert "BWS_SERVER_URL" not in env
    env = {"BWS_SERVER_URL": "http://evil.example"}
    assert bw.apply_server_url_to_env(env, "") is not None
    assert "BWS_SERVER_URL" not in env
    env = {}
    assert bw.apply_server_url_to_env(env, "https://vault.bitwarden.eu") is None
    assert env["BWS_SERVER_URL"] == "https://vault.bitwarden.eu"


def test_fetch_rejects_bad_url():
    with pytest.raises(RuntimeError):
        bw.fetch_bitwarden_secrets(access_token="tok", project_id="proj", server_url="http://evil.example", use_cache=False)
