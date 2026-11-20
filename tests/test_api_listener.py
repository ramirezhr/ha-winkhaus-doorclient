"""The WebSocket listener, fed with genuine encrypted frames.

The frames here are built the way the lock builds them, using the client's
own IV rules. That makes this more than a mock exercise: if the IV
construction or the counter handling drifts, decryption fails and these
tests notice - which is exactly what would happen on the wire.
"""

import asyncio
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

    async def test_an_interleaved_push_does_not_drop_the_reply(self, client):
        """The lock pushes state between the fragments of a reply.

        Not a hypothetical: measured on a blueMotion+ (firmware 1.6.2) on
        2026-11-15. A 218 s capture recorded 70 replies of ~8.4 fragments and
        28 state pushes, and caught two pushes landing mid-reply, with 632 and
        17 bytes already buffered.

        Both types arrive on the same socket, so each is reassembled on its own
        buffer. Sharing one buffer truncated the reply - in the 632 byte case
        that much of an answer was thrown away - and left the remaining
        fragments to fail decoding.
        """
        received = await listen_to(
            client,
            [
                frame(client, '{"XC_SUC":{"sta', 1, final=False, packet_type=6),
                frame(client, {"XC_SUC": {"state": "open"}}, 2, packet_type=1),
                frame(client, 'te":"closed"}}', 3, packet_type=6),
            ],
        )

        assert received == [
            [{"name": "state", "value": "open"}],    # the push
            [{"name": "state", "value": "closed"}],  # the reply, still intact
        ]

    async def test_a_message_with_invalid_utf8_does_not_poison_its_type(self, client):
        """One undecodable message must not cost the rest of the session.

        The buffer used to be retired after decoding, so a lone bad byte
        stayed in it and every later message of that packet type failed on
        the same byte until the WebSocket was rebuilt.
        """
        received = await listen_to(
            client,
            [
                frame(client, b'{"XC_SUC":{"name":"\xff"}}', 1, packet_type=6),
                frame(client, {"XC_SUC": {"state": "open"}}, 2, packet_type=6),
            ],
        )

        assert received == [[{"name": "state", "value": "open"}]]

    async def test_the_buffer_is_empty_afterwards(self, client):
        await listen_to(client, [frame(client, {"XC_SUC": {"state": "open"}}, 1)])

        assert client._rx_buffers == {}


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
        assert all(len(buffer) <= 65536 for buffer in client._rx_buffers.values())

    async def test_an_error_payload_that_is_not_a_dict(self, client, caplog):
        """Firmware could report a bare string instead of the usual object."""
        await listen_to(client, [frame(client, {"XC_ERR": "motor jammed"}, 1)])

        assert "motor jammed" in caplog.text


class TestReplyCorrelation:
    """Which messages belong to a waiting request, and which do not.

    The packet type is the only thing that separates them. A reply and an
    unsolicited push can both carry a filled state object, so the shape says
    nothing - a push handed to a waiting reader would be taken for the answer,
    and the real answer would then be handed to the request after it.
    """

    async def test_a_reply_resolves_the_waiting_request(self, client):
        waiting = asyncio.get_running_loop().create_future()
        client._ws_reply = waiting

        await listen_to(
            client,
            [frame(client, {"XC_SUC": {"firmware": "1.6.2"}}, 1, packet_type=6)],
        )

        assert waiting.done()
        assert json.loads(waiting.result()) == {"XC_SUC": {"firmware": "1.6.2"}}

    async def test_a_push_does_not_resolve_a_waiting_request(self, client):
        """The push carries state and mode, exactly like the answer to
        getStates would, and still must not be mistaken for it."""
        waiting = asyncio.get_running_loop().create_future()
        client._ws_reply = waiting

        received = await listen_to(
            client,
            [frame(client, {"state": "closed", "mode": "day"}, 1, packet_type=1)],
        )

        assert not waiting.done()
        assert received == [
            [{"name": "state", "value": "closed"}, {"name": "mode", "value": "day"}]
        ]

    async def test_a_reply_with_nobody_waiting_goes_to_the_dispatch(self, client):
        """What happens to the answers of the status nudges: no caller asked
        for them, so they are treated as a state update."""
        received = await listen_to(
            client,
            [frame(client, {"XC_SUC": {"state": "open", "mode": "night"}}, 1, packet_type=6)],
        )

        assert client._ws_reply is None
        assert received == [
            [{"name": "state", "value": "open"}, {"name": "mode", "value": "night"}]
        ]

    async def test_a_fragmented_reply_resolves_only_when_complete(self, client):
        waiting = asyncio.get_running_loop().create_future()
        client._ws_reply = waiting

        await listen_to(
            client,
            [
                frame(client, '{"XC_SUC":{"na', 1, final=False, packet_type=6),
                frame(client, 'me":"Haustuer"}}', 2, packet_type=6),
            ],
        )

        assert json.loads(waiting.result()) == {"XC_SUC": {"name": "Haustuer"}}


class TestRejectionWording:
    """A refusal has to name a cause, whichever field the firmware fills in."""

    async def test_the_code_is_logged_when_there_is_no_text(self, client, caplog):
        await listen_to(client, [frame(client, {"XC_ERR": {"code": "E_BUSY"}}, 1)])

        assert "E_BUSY" in caplog.text
        assert "rejected" in caplog.text.lower()

    async def test_an_empty_text_does_not_hide_the_code(self, client, caplog):
        await listen_to(client, [frame(client, {"XC_ERR": {"text": "", "code": 4711}}, 1)])

        assert "4711" in caplog.text

    async def test_text_is_preferred_over_the_code(self, client, caplog):
        await listen_to(client, [frame(client, {"XC_ERR": {"text": "blocked", "code": 4711}}, 1)])

        assert "blocked" in caplog.text
        assert "4711" not in caplog.text

    async def test_a_zero_code_reaches_the_log(self, client, caplog):
        """Same reasoning as on the HTTP path: a zero is a reported code."""
        await listen_to(client, [frame(client, {"XC_ERR": {"code": 0}}, 1)])

        rejections = [
            item.getMessage() for item in caplog.records
            if "rejected" in item.getMessage()
        ]
        assert len(rejections) == 1
        assert rejections[0].endswith(": 0")


class TestListenerGuard:
    """The listener decrypts, so it needs the key material the handshake sets.

    Starting it without that material would fail once per incoming message
    instead of once, and the first failure would be logged as a decode error
    rather than as what it is. The guard has been there since the hybrid
    transport landed and was never covered.
    """

    async def test_it_refuses_to_start_without_a_shared_key(self, client):
        client.shared_key = None

        with pytest.raises(RuntimeError, match="before the handshake"):
            await client._listen(FakeSocket([]))

    async def test_it_refuses_to_start_without_a_device_challenge(self, client):
        client.device_challenge = None

        with pytest.raises(RuntimeError, match="before the handshake"):
            await client._listen(FakeSocket([]))
