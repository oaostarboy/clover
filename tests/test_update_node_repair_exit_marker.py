"""Failed current-checkout Node repair must report failure to gateway caller."""
from clover_cli import update_cmd


def test_gateway_node_repair_failure_writes_failed_exit_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(update_cmd, 'get_clover_home', lambda: tmp_path)
    monkeypatch.setattr(update_cmd, '_update_node_dependencies', lambda: ['workspace'])
    result = update_cmd._repair_node_deps_on_current_checkout(lambda _: None, gateway_mode=True)
    assert result is False
    assert (tmp_path / '.update_exit_code').read_text().strip() == '1'
