"""Sending commands: WebSocket first, HTTP as the fallback.

This is the hybrid dispatch. Which path a command takes decides how fast it
arrives and whether a state update follows on its own, so both are pinned.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from custom_components.winkhaus_doorclient.api import DoorClient

SERIAL = "WH_021C6920F1B188"
STATES = [{"name": "state", "value": "closed"}, {"name": "mode", "value": "day"}]


@pytest.fixture
def client() -> DoorClient:
    return DoorClient(serial_number=SERIAL, ip="10.10.30.197", password="secret", session=MagicMock(), ssl_context=MagicMock())


@pytest.fixture
def connected(client) -> DoorClient:
    """A client with a live WebSocket that records what was sent."""
    client.ws_connected = True
    client._active_ws = AsyncMock()
    client.shared_key = b"k" * 32
    client.client_challenge = bytes(range(32))
    client.device_challenge = bytes(range(32, 64))
    return client


def sent_frame(client) -> bytes:
    return client._active_ws.send.call_args.args[0]


def decrypt_frame(client, frame: bytes) -> str:
    """Undo what async_send_payload did, using the client's own IV rules."""
    counter = int.from_bytes(frame[4:8], "big")
    iv = client._get_iv(client.client_challenge, counter)
    return AESCCM(client.shared_key, tag_length=16).decrypt(iv, frame[8:], None).decode()


# ------------------------------------------------------------ dispatch path

class TestSendPayload:
    async def test_uses_the_websocket_when_connected(self, connected):
        assert await connected.async_send_payload("/api/v1/control", {"command": "night"})
        connected._active_ws.send.assert_awaited_once()

    async def test_frame_carries_endpoint_and_payload(self, connected):
        await connected.async_send_payload("/api/v1/control", {"command": "night"})

        endpoint, payload = decrypt_frame(connected, sent_frame(connected)).split("\n", 1)
        assert endpoint == "/api/v1/control"
        assert json.loads(payload) == {"command": "night"}

    async def test_header_marks_a_final_client_frame(self, connected):
        """0x85 is the FIN bit plus packet type 5, what the client sends."""
        await connected.async_send_payload("/api/v1/control", {"command": "day"})

        frame = sent_frame(connected)
        assert frame[0] == 0x85
        assert frame[1] == 0x00

    async def test_declared_length_matches_the_ciphertext(self, connected):
        await connected.async_send_payload("/api/v1/control", {"command": "day"})

        frame = sent_frame(connected)
        assert int.from_bytes(frame[2:4], "big") == len(frame) - 8

    async def test_counter_advances_with_every_frame(self, connected):
        counters = []
        for _ in range(3):
            await connected.async_send_payload("/api/v1/getStates", {})
            counters.append(int.from_bytes(sent_frame(connected)[4:8], "big"))

        assert counters == sorted(set(counters))
        assert len(counters) == 3

    async def test_empty_payload_becomes_an_empty_object(self, connected):
        await connected.async_send_payload("/api/v1/getStates", {})

        _, payload = decrypt_frame(connected, sent_frame(connected)).split("\n", 1)
        assert payload == "{}"

    async def test_the_request_is_remembered(self, connected):
        await connected.async_send_payload("/api/v1/control", {"command": "night"})

        endpoint, payload, _ = connected._last_request
        assert endpoint == "/api/v1/control"
        assert payload == {"command": "night"}


class TestHttpFallback:
    async def test_used_when_no_websocket(self, client):
        with patch.object(client, "_request", return_value={}) as request:
            assert await client.async_send_payload("/api/v1/control", {"command": "day"})

        assert request.call_args.args[0] == "/api/v1/control"

    async def test_used_when_the_websocket_send_fails(self, connected):
        connected._active_ws.send.side_effect = OSError("socket gone")

        with patch.object(connected, "_request", return_value={}) as request:
            assert await connected.async_send_payload("/api/v1/control", {"command": "day"})

        request.assert_called_once()

    async def test_failure_on_both_paths_is_reported(self, connected):
        connected._active_ws.send.side_effect = OSError("socket gone")

        with patch.object(connected, "_request", side_effect=OSError("no route")):
            assert await connected.async_send_payload("/api/v1/control", {"command": "day"}) is False

    async def test_http_only_failure_is_reported(self, client):
        with patch.object(client, "_request", side_effect=OSError("no route")):
            assert await client.async_send_payload("/api/v1/control", {"command": "day"}) is False


# ---------------------------------------------------------------- commands

class TestExecuteCommand:
    @pytest.mark.parametrize(
        ("command", "value", "expected"),
        [
            ("mode", "day", "day"),
            ("mode", "night", "night"),
            ("lock", None, "night"),
            ("unlock", None, "day"),
            ("open", None, "unlock"),
        ],
    )
    async def test_commands_reach_the_lock(self, connected, command, value, expected):
        await connected.async_execute_command(command, value)

        _, payload = decrypt_frame(connected, sent_frame(connected)).split("\n", 1)
        assert json.loads(payload) == {"command": expected}

    @pytest.mark.parametrize(
        ("command", "value"),
        [("mode", None), ("mode", "tag"), ("bogus", None), ("MODE", "day")],
    )
    async def test_invalid_input_never_reaches_the_lock(self, connected, command, value):
        assert await connected.async_execute_command(command, value) is False
        connected._active_ws.send.assert_not_awaited()

    async def test_http_path_fetches_the_new_state(self, client):
        """Without a WebSocket nothing pushes, so the state is pulled once."""
        with patch.object(client, "_request", return_value={}), patch.object(
            client, "get_states", return_value=STATES
        ) as get_states, patch("asyncio.sleep", new_callable=AsyncMock):
            received = []
            client.on_state_change = received.append

            await client.async_execute_command("lock")

        get_states.assert_called_once()
        assert received == [STATES]

    async def test_websocket_path_waits_for_the_push(self, connected):
        """The lock reports the change itself, so nothing is pulled."""
        with patch.object(connected, "get_states") as get_states:
            await connected.async_execute_command("lock")

        get_states.assert_not_called()

    async def test_no_callback_means_no_crash(self, client):
        with patch.object(client, "_request", return_value={}), patch.object(
            client, "get_states", return_value=STATES
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            client.on_state_change = None
            assert await client.async_execute_command("lock")


class TestUnblock:
    async def test_sends_to_the_unblock_endpoint(self, connected):
        assert await connected.async_unblock()

        endpoint, _ = decrypt_frame(connected, sent_frame(connected)).split("\n", 1)
        assert endpoint == "/api/v1/unblock"

    async def test_reports_a_refusal(self, client):
        with patch.object(client, "_request", side_effect=OSError("no route")):
            assert await client.async_unblock() is False


# -------------------------------------------------------------------- stop

class TestStop:
    async def test_closes_the_websocket(self, connected):
        # stop() clears the reference, so hold on to it first
        websocket = connected._active_ws

        await connected.stop()

        websocket.close.assert_awaited_once()
        assert connected.ws_connected is False
        assert connected._active_ws is None

    async def test_clears_the_session(self, connected):
        connected.current_session_start = 1234.0
        await connected.stop()

        assert connected.current_session_start is None

    async def test_stops_the_monitor_loop(self, connected):
        connected._monitor_running = True
        await connected.stop()

        assert connected._monitor_running is False

    async def test_cancels_the_watchdog(self, connected):
        task = MagicMock()
        connected._watchdog_task = task

        await connected.stop()

        task.cancel.assert_called_once()

    async def test_survives_a_websocket_that_will_not_close(self, connected):
        connected._active_ws.close.side_effect = OSError("already gone")
        await connected.stop()

        assert connected.ws_connected is False

    async def test_works_without_a_connection(self, client):
        await client.stop()
        assert client._monitor_running is False


class TestSessionOwnership:
    async def test_the_shared_session_is_left_open(self, connected):
        """Home Assistant owns the session; closing it would break every
        other integration sharing it."""
        await connected.stop()

        connected._session.close.assert_not_called()

    async def test_reloading_does_not_exhaust_the_session(self, connected):
        """An entry may be reloaded many times over a Home Assistant run."""
        for _ in range(5):
            await connected.stop()

        connected._session.close.assert_not_called()
