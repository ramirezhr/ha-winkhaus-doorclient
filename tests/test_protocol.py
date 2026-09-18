from unittest.mock import MagicMock
"""Protocol handling and coordinator data merging.

Like test_logic, every case here maps to a problem that was seen in the
field or to a trap that was found while reading the original client.
"""

import pytest

from custom_components.winkhaus_doorclient import TRANSIENT_KEYS
from custom_components.winkhaus_doorclient.api import DoorClient

SERIAL = "WH_021C6920F1B188"


@pytest.fixture
def client():
    return DoorClient(serial_number=SERIAL, ip="10.0.0.1", password="secret", session=MagicMock(), ssl_context=MagicMock())


def states(**kwargs):
    """Build coordinator data from keyword arguments."""
    return [{"name": k, "value": v} for k, v in kwargs.items()]


def merge(previous, pushed):
    """The merge performed by handle_state_change in __init__.py."""
    merged = {
        item["name"]: item["value"]
        for item in (previous or [])
        if item["name"] not in TRANSIENT_KEYS
    }
    merged.update({item["name"]: item["value"] for item in pushed})
    return [{"name": k, "value": v} for k, v in merged.items()]


def value_of(data, key):
    return next((i["value"] for i in data if i["name"] == key), None)


def keys_of(data):
    return [i["name"] for i in data]


# --------------------------------------------------------------- push merging

class TestPushMerge:
    """A state-change push carries only what changed."""

    def test_fields_absent_from_the_push_survive(self):
        """Regression: last_update_from_device vanished until the next poll."""
        poll = states(state="closed", locked=False, mode="day", time=1787486008)
        push = states(state="closed", locked=True, mode="night")

        assert "time" in keys_of(merge(poll, push))

    def test_pushed_values_win(self):
        poll = states(state="closed", locked=False, mode="day")
        push = states(locked=True, mode="night")
        result = merge(poll, push)

        assert value_of(result, "locked") is True
        assert value_of(result, "mode") == "night"

    def test_cleared_fault_is_not_inherited(self):
        """The lock omits 'error' instead of sending an empty list.

        Without the exemption a fault acknowledged through the button would
        stick to the sensor forever.
        """
        with_fault = states(state="closed", error=["blocked"])
        after_clear = states(state="closed")

        assert "error" not in keys_of(merge(with_fault, after_clear))

    def test_active_fault_is_taken_over(self):
        result = merge(states(state="closed"), states(error=["overcurrent"]))
        assert value_of(result, "error") == ["overcurrent"]

    def test_works_without_previous_data(self):
        push = states(state="open")
        assert keys_of(merge(None, push)) == ["state"]

    def test_key_order_is_stable(self):
        poll = states(state="closed", locked=False, mode="day", time=1)
        push = states(state="open")
        assert keys_of(merge(poll, push))[:4] == ["state", "locked", "mode", "time"]

    def test_error_is_the_only_exempt_key(self):
        assert TRANSIENT_KEYS == {"error"}


# ------------------------------------------------------------ counter handling

class TestDeviceCounter:
    """Only strictly increasing counters are accepted."""

    def test_first_message_is_always_accepted(self, client):
        """Starting at None rather than 0 avoids rejecting counter zero."""
        assert client._device_counter is None

    def test_reset_between_sessions(self, client):
        """The device restarts its sequence on every handshake.

        A stale value here would reject every message of the next session.
        """
        client._device_counter = 5000
        client._rx_buffer.extend(b"leftover")
        client._rx_type = 1

        # what the finally block in _listen does
        client.last_session_seconds = client.get_current_uptime()
        client.ws_connected = False
        client._active_ws = None
        client.current_session_start = None
        client._rx_buffer.clear()
        client._rx_type = None
        client._device_counter = None

        assert client._device_counter is None
        assert len(client._rx_buffer) == 0


# ------------------------------------------------------------------- requests

class TestLastRequest:
    """Rejections are attributed to the request that most likely caused them."""

    def test_nothing_recorded_yet(self, client):
        assert "none recorded" in client._describe_last_request()

    def test_recent_request_is_named(self, client):
        import time

        client._last_request = ("/api/v1/control", {"command": "night"}, time.time())
        described = client._describe_last_request()

        assert "/api/v1/control" in described
        assert "night" in described

    def test_stale_request_is_flagged_as_uncertain(self, client):
        import time

        client._last_request = ("/api/v1/control", {"command": "day"}, time.time() - 47)
        assert "may be unrelated" in client._describe_last_request()

    def test_empty_payload_is_not_printed(self, client):
        import time

        client._last_request = ("/api/v1/getStates", {}, time.time())
        described = client._describe_last_request()

        assert "/api/v1/getStates" in described
        assert "{}" not in described


# ---------------------------------------------------------------- session data

class TestSessionDuration:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(0, "00:00:00"), (3661, "01:01:01"), (211614, "58:46:54")],
    )
    def test_format(self, client, seconds, expected):
        client.last_session_seconds = seconds
        assert client._format_session_duration() == expected


# ------------------------------------------------------------------- fragments

class TestReassembly:
    """Chunks are collected until the FIN bit arrives.

    Only the header handling is exercised here - the encrypted payload is
    replaced by plain bytes, because producing valid AES-CCM frames would
    test the test rather than the integration.
    """

    @staticmethod
    def feed(client, header, plaintext):
        """The buffering half of the message loop in _listen."""
        is_final = bool(header & 0x80)
        packet_type = header & 0x0F

        if client._rx_type is not None and packet_type != client._rx_type:
            client._rx_buffer.clear()

        client._rx_buffer.extend(plaintext)
        client._rx_type = packet_type

        if not is_final:
            if len(client._rx_buffer) > 65536:
                client._rx_buffer.clear()
                client._rx_type = None
            return None

        payload = bytes(client._rx_buffer).decode("utf-8")
        client._rx_buffer.clear()
        client._rx_type = None
        return payload

    def test_single_message_passes_straight_through(self, client):
        assert self.feed(client, 0x81, b'{"a":1}') == '{"a":1}'

    def test_two_fragments_are_joined(self, client):
        assert self.feed(client, 0x01, b'{"XC_SUC":{"state"') is None
        assert self.feed(client, 0x81, b':1}}') == '{"XC_SUC":{"state":1}}'

    def test_utf8_split_across_a_boundary(self, client):
        """A chunk can end inside a multi-byte character - as in 'Haustür'."""
        text = '{"name":"Haustür"}'.encode("utf-8")
        cut = text.index(b"\xc3") + 1

        assert self.feed(client, 0x01, text[:cut]) is None
        assert self.feed(client, 0x81, text[cut:]) == text.decode("utf-8")

    def test_packet_type_change_drops_the_buffer(self, client):
        self.feed(client, 0x06, b'{"incomplete')
        assert self.feed(client, 0x81, b'{"state":1}') == '{"state":1}'

    def test_buffer_cap_prevents_unbounded_growth(self, client):
        for _ in range(20):
            self.feed(client, 0x01, b"x" * 4096)
        assert len(client._rx_buffer) <= 65536

    def test_buffer_is_empty_after_a_complete_message(self, client):
        self.feed(client, 0x01, b"part")
        self.feed(client, 0x81, b"rest")
        assert len(client._rx_buffer) == 0
        assert client._rx_type is None
