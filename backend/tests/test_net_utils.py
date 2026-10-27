"""Mutation callers must retain occupied/unknown listener observations."""
import socket
from unittest.mock import patch

import psutil
import pytest

from net_utils import find_port_holder, holder_via_lsof, holder_via_psutil, port_accepts_connections


def test_pidless_listener_evidence_survives_failed_fallbacks():
    observed = {"pid": None, "name": "unknown", "addr": "::1"}
    with (patch('net_utils.holder_via_psutil', return_value=observed),
          patch('net_utils.holder_via_lsof', return_value=None),
          patch('net_utils.port_accepts_connections', return_value=False)):
        assert find_port_holder(12345) == observed


def test_failed_enumeration_and_connect_is_unknown_not_free():
    unknown = {"pid": None, "name": "unknown", "addr": "", "observation": "unknown"}
    with (patch('net_utils.holder_via_psutil', return_value=unknown),
          patch('net_utils.holder_via_lsof', return_value=unknown),
          patch('net_utils.port_accepts_connections', return_value=False)):
        assert find_port_holder(12345)['observation'] == 'unknown'


def test_successful_empty_enumeration_can_confirm_free():
    with (patch('net_utils.holder_via_psutil', return_value=None),
          patch('net_utils.holder_via_lsof', return_value={'observation': 'unknown'}),
          patch('net_utils.port_accepts_connections', return_value=False)):
        assert find_port_holder(12345) is None


def test_ipv6_loopback_listener_is_occupied():
    try:
        listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        listener.bind(('::1', 0))
        listener.listen()
    except OSError:
        pytest.skip('IPv6 loopback unavailable')
    try:
        assert port_accepts_connections(listener.getsockname()[1]) is True
    finally:
        listener.close()


def test_enumeration_failure_is_preserved_as_unknown():
    with patch('net_utils.psutil.net_connections', side_effect=psutil.AccessDenied()):
        assert holder_via_psutil(12345)['observation'] == 'unknown'
    with patch('net_utils.subprocess.run', side_effect=FileNotFoundError()):
        assert holder_via_lsof(12345)['observation'] == 'unknown'
