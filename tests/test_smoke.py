import socket

import pytest

import fielddesk


def test_package_imports() -> None:
    assert fielddesk.__version__


def test_network_is_blocked() -> None:
    with pytest.raises(Exception, match=r"(?i)socket"):
        socket.create_connection(("203.0.113.10", 443), timeout=1)  # TEST-NET-3, no DNS
