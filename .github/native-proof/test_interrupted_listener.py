import socket
from unittest.mock import patch
import pytest
import interrupted_controller as c


def test_private_listener_probe_never_scans_process_table():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        listener.listen()
        with patch('psutil.net_connections', side_effect=AssertionError('host scan forbidden')):
            with pytest.raises(c.Refusal):
                c.require_fixture_port_closed(port)
    with patch('psutil.net_connections', side_effect=AssertionError('host scan forbidden')):
        c.require_fixture_port_closed(port)
