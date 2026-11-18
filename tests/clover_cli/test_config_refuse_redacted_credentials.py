import pytest

from clover_cli.config_credential_values import (
    is_redacted_credential_value, refuse_redacted_config_credentials)


@pytest.mark.parametrize("v", ["***", "sk-pro...WXYZ", "sk-p...7890", "[REDACTED]", "Bearer ***", "sk-****WXYZ"])
def test_masks_detected(v):
    assert is_redacted_credential_value(v)


@pytest.mark.parametrize("v", ["sk-realkey1234567890abcdef", "${OPENAI_API_KEY}", "", "hunter2"])
def test_real_values_pass(v):
    assert not is_redacted_credential_value(v)


def test_save_env_value_refuses_mask(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOVER_HOME", str(tmp_path))
    from clover_cli.config import save_env_value, load_env
    save_env_value("OPENAI_API_KEY", "sk-realkey1234567890abcdef")
    with pytest.raises(ValueError, match="redacted"):
        save_env_value("OPENAI_API_KEY", "sk-rea...cdef")
    assert load_env()["OPENAI_API_KEY"] == "sk-realkey1234567890abcdef"


def test_config_refuses_new_mask_but_keeps_preexisting():
    with pytest.raises(ValueError):
        refuse_redacted_config_credentials({"model": {"api_key": "***"}}, lambda: {"model": {"api_key": "real"}})
    refuse_redacted_config_credentials({"model": {"api_key": "***"}}, lambda: {"model": {"api_key": "***"}})
    refuse_redacted_config_credentials({"model": {"api_key": "real"}}, lambda: {})
