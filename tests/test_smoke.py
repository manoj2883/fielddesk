import socket

import pytest

import fielddesk


def test_package_imports() -> None:
    assert fielddesk.__version__


def test_network_is_blocked() -> None:
    with pytest.raises(Exception, match=r"(?i)socket"):
        socket.create_connection(("api.anthropic.com", 443), timeout=1)
