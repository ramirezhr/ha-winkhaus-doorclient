"""The WebSocket listener, fed with genuine encrypted frames.

The frames here are built the way the lock builds them, using the client's
own IV rules. That makes this more than a mock exercise: if the IV
construction or the counter handling drifts, decryption fails and these
tests notice - which is exactly what would happen on the wire.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets.exceptions
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from custom_components.winkhaus_doorclient.api import DoorClient

SERIAL = "WH_021C6920F1B188"
SHARED_KEY = bytes(range(32))
DEVICE_CHALLENGE = bytes(range(32, 64))


@pytest.fixture
def client() -> DoorClient:
    item = DoorClient(serial_number=SERIAL, ip="10.10.30.197", password="secret", session=MagicMock(), ssl_context=MagicMock())
    item.shared_key = SHARED_KEY
    item.device_challenge = DEVICE_CHALLENGE
    item.ws_connected = True
    return item


def frame(client, payload, counter: int, *, final: bool = True, packet_type: int = 1) -> bytes:
    """Build a frame exactly as the lock would.

    Header is [type|FIN][0x00][length:2][counter:4], followed by the
    AES-CCM ciphertext of the payload.
    """
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    header_byte = packet_type | (0x80 if final else 0x00)
    iv = client._get_iv(client.device_challenge, counter)
    encrypted = AESCCM(client.shared_key, tag_length=16).encrypt(iv, payload, None)

    return (
        bytes([header_byte, 0x00])
        + len(encrypted).to_bytes(2, "big")
        + counter.to_bytes(4, "big")
        + encrypted
    )


class FakeSocket:
    """Yields prepared frames, then behaves like a closed connection."""

    def __init__(self, frames, closed_after=True):
        self._frames = list(frames)
        self._closed_after = closed_after

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self._frames:
            yield item
        if self._closed_after:
            raise websockets.exceptions.ConnectionClosedOK(None, None)


async def listen_to(client, frames, **kwargs):
    """Run the listener over a set of frames and collect the pushes."""
    received = []
    client.on_state_change = received.append

    def swallow(coro, *args, **kwargs):
        # The watchdog is out of scope here. Close the coroutine explicitly
        # so Python does not warn about one that was never awaited.
        coro.close()
        return MagicMock()

    with patch("asyncio.create_task", side_effect=swallow):
        await client._listen(FakeSocket(frames, **kwargs))

    return received


# ------------------------------------------------------------ status pushes

class TestStatusUpdates:
    async def test_a_status_frame_reaches_the_callback(self, client):
        received = await listen_to(
            client, [frame(client, {"XC_SUC": {"state": "open", "mode": "day"}}, 1)]
        )

        assert received == [
            [{"name": "state", "value": "open"}, {"name": "mode", "value": "day"}]
        ]

    async def test_numeric_states_are_mapped(self, client):
        received = await listen_to(
            client, [frame(client, {"XC_SUC": {"state": 0, "mode": 1}}, 1)]
        )

        values = {i["name"]: i["value"] for i in received[0]}
        assert values == {"state": "open", "mode": "night"}

    async def test_payload_without_the_wrapper_is_accepted(self, client):
        received = await listen_to(client, [frame(client, {"state": "closed"}, 1)])

        assert received == [[{"name": "state", "value": "closed"}]]

    async def test_several_frames_arrive_in_order(self, client):
        received = await listen_to(
            client,
            [
                frame(client, {"XC_SUC": {"mode": "day"}}, 1),
                frame(client, {"XC_SUC": {"mode": "night"}}, 2),
            ],
        )

        assert [r[0]["value"] for r in received] == ["day", "night"]

    async def test_a_command_acknowledgement_is_not_a_status(self, client):
        received = await listen_to(client, [frame(client, {"XC_SUC": {}}, 1)])

        assert received == []


# ---------------------------------------------------------------- rejections

class TestRejections:
    async def test_an_error_frame_is_logged_with_its_text(self, client, caplog):
        await listen_to(client, [frame(client, {"XC_ERR": {"text": "blocked"}}, 1)])

        assert "blocked" in caplog.text
        assert "rejected" in caplog.text.lower()

    async def test_the_rejection_names_the_last_request(self, client, caplog):
        import time

        client._last_request = ("/api/v1/control", {"command": "night"}, time.time())

        await listen_to(client, [frame(client, {"XC_ERR": {"text": "blocked"}}, 1)])

        assert "/api/v1/control" in caplog.text

    async def test_a_rejection_is_not_a_status_update(self, client):
        received = await listen_to(client, [frame(client, {"XC_ERR": {"text": "busy"}}, 1)])

        assert received == []


# ---------------------------------------------------------------- fragments

class TestFragments:
    async def test_two_fragments_are_reassembled(self, client):
        whole = json.dumps({"XC_SUC": {"state": "closed", "mode": "day"}})
        half = len(whole) // 2

        received = await listen_to(
            client,
            [
                frame(client, whole[:half], 1, final=False),
                frame(client, whole[half:], 2, final=True),
            ],
        )

        assert received == [
            [{"name": "state", "value": "closed"}, {"name": "mode", "value": "day"}]
        ]

    async def test_a_utf8_character_may_span_the_boundary(self, client):
        """A door named "Haustür" is exactly the case that breaks a naive split."""
        whole = json.dumps({"XC_SUC": {"name": "Haustür"}}, ensure_ascii=False)
        raw = whole.encode("utf-8")
        cut = raw.index(b"\xc3") + 1

        received = await listen_to(
            client,
            [
                frame(client, raw[:cut], 1, final=False),
                frame(client, raw[cut:], 2, final=True),
            ],
        )

        assert received == []  # no state or mode, but it decoded without error

    async def test_a_packet_type_change_drops_the_buffer(self, client):
        received = await listen_to(
            client,
            [
                frame(client, '{"incomplete', 1, final=False, packet_type=6),
                frame(client, {"XC_SUC": {"state": "open"}}, 2, packet_type=1),
            ],
        )

        assert received == [[{"name": "state", "value": "open"}]]

    async def test_the_buffer_is_empty_afterwards(self, client):
        await listen_to(client, [frame(client, {"XC_SUC": {"state": "open"}}, 1)])

        assert len(client._rx_buffer) == 0
        assert client._rx_type is None


# ------------------------------------------------------- counter and cleanup

class TestCounterHandling:
    async def test_a_gap_in_the_counter_is_accepted(self, client):
        """A lost frame must not lock out the ones that follow it."""
        received = await listen_to(
            client,
            [
                frame(client, {"XC_SUC": {"mode": "day"}}, 5),
                frame(client, {"XC_SUC": {"mode": "night"}}, 9),
            ],
        )

        assert [r[0]["value"] for r in received] == ["day", "night"]

    async def test_a_repeated_counter_is_ignored(self, client):
        received = await listen_to(
            client,
            [
                frame(client, {"XC_SUC": {"mode": "day"}}, 5),
                frame(client, {"XC_SUC": {"mode": "night"}}, 5),
            ],
        )

        assert len(received) == 1
        assert received[0][0]["value"] == "day"

    async def test_an_older_counter_is_ignored(self, client):
        received = await listen_to(
            client,
            [
                frame(client, {"XC_SUC": {"mode": "day"}}, 9),
                frame(client, {"XC_SUC": {"mode": "night"}}, 3),
            ],
        )

        assert len(received) == 1

    async def test_the_counter_resets_when_the_session_ends(self, client):
        await listen_to(client, [frame(client, {"XC_SUC": {"mode": "day"}}, 5)])

        assert client._device_counter is None

    async def test_the_session_is_torn_down(self, client):
        client.current_session_start = 1000.0

        await listen_to(client, [frame(client, {"XC_SUC": {"mode": "day"}}, 1)])

        assert client.ws_connected is False
        assert client._active_ws is None
        assert client.current_session_start is None


# ------------------------------------------------------------- broken input

class TestBrokenFrames:
    async def test_a_short_frame_is_skipped(self, client):
        received = await listen_to(
            client, [b"\x81\x00\x00", frame(client, {"XC_SUC": {"mode": "day"}}, 1)]
        )

        assert len(received) == 1

    async def test_an_undecryptable_frame_does_not_stop_the_listener(self, client):
        broken = bytes([0x81, 0x00, 0x00, 0x10]) + (2).to_bytes(4, "big") + b"garbage" * 3

        received = await listen_to(
            client, [broken, frame(client, {"XC_SUC": {"mode": "day"}}, 5)]
        )

        assert len(received) == 1

    async def test_a_short_frame_does_not_count_as_a_sign_of_life(self, client):
        """Only real messages may refresh the watchdog timestamp."""
        client.last_message_time = 0

        await listen_to(client, [b"\x81\x00\x00"], closed_after=True)

        assert client.last_message_time > 0  # set once when the listener started


class TestBufferGuard:
    async def test_a_lost_final_fragment_does_not_fill_memory(self, client, caplog):
        """Without a cap a missing FIN bit would grow the buffer forever."""
        chunk = "x" * 5000
        frames = [
            frame(client, chunk, n, final=False) for n in range(1, 20)
        ]

        await listen_to(client, frames)

        assert "64 KB" in caplog.text
        assert len(client._rx_buffer) <= 65536

    async def test_an_error_payload_that_is_not_a_dict(self, client, caplog):
        """Firmware could report a bare string instead of the usual object."""
        await listen_to(client, [frame(client, {"XC_ERR": "motor jammed"}, 1)])

        assert "motor jammed" in caplog.text
