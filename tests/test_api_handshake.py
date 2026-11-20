"""The handshake, played against a simulated lock.

The fake server here performs the real key exchange: a genuine X25519
keypair, a genuine ECDH, and an answer computed the way the lock computes
it. So this checks that our side of the protocol actually agrees with a
counterpart - not merely that the code runs.
"""

import asyncio
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets.exceptions
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from custom_components.winkhaus_doorclient.api import DoorClient

SERIAL = "WH_021C6920F1B188"
PASSWORD = "secret"
CLIENT_CHALLENGE = bytes(range(100, 132))
DEVICE_CHALLENGE = bytes(range(32, 64))


@pytest.fixture
def client() -> DoorClient:
    return DoorClient(serial_number=SERIAL, ip="10.10.30.197", password=PASSWORD, session=MagicMock(), ssl_context=MagicMock())


class FakeLock:
    """Answers a handshake the way the device does.

    `authenticate` decides whether the final proof matches, which is the
    only knob a test needs to turn.
    """

    def __init__(self, client, *, authenticate=True, greeting_prefix=b"", listen_frames=None):
        self._private = x25519.X25519PrivateKey.generate()
        self._client = client
        self._authenticate = authenticate
        self._greeting_prefix = greeting_prefix
        self._listen_frames = listen_frames or []

        public = self._private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.greeting = greeting_prefix + public + DEVICE_CHALLENGE
        self.received = []
        self.closed = False

        # What the client will derive once it has done its half of the ECDH
        self.shared_key = None

    async def recv(self):
        if not self.received:
            return self.greeting

        # Second call: prove we know the password
        proof = self._client._create_hmac_sha1(
            self._client._get_pbdf2_key(), CLIENT_CHALLENGE
        )
        return proof if self._authenticate else b"wrong-proof-entirely"

    async def send(self, data):
        self.received.append(data)
        # The client's public key comes first; complete the exchange
        if len(self.received) == 1:
            self.shared_key = self._private.exchange(
                x25519.X25519PublicKey.from_public_bytes(data[4:36])
            )
            return

        # Everything after the handshake is a request, and the lock answers
        # every request. Staying silent here would make the client wait out
        # its timeout instead of getting on with the session.
        waiting = self._client._ws_reply
        if waiting is not None and not waiting.done():
            waiting.set_result('{"XC_SUC": {}}')

    async def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self._listen_frames:
            yield item
        raise websockets.exceptions.ConnectionClosedOK(None, None)


async def run_once(client, lock):
    """Let connect_and_monitor complete exactly one connection attempt."""
    original = client._listen

    async def listen_then_stop(ws):
        client._monitor_running = False
        return await original(ws)

    def swallow(coro, *args, **kwargs):
        # cancel() is synchronous, so this must not be an AsyncMock
        coro.close()
        return MagicMock()

    with patch(
        "custom_components.winkhaus_doorclient.api.websockets.connect", return_value=lock
    ), patch(
        "custom_components.winkhaus_doorclient.api.os.urandom", return_value=CLIENT_CHALLENGE
    ), patch.object(
        client, "_listen", side_effect=listen_then_stop
    ), patch("asyncio.create_task", side_effect=swallow), patch(
        "asyncio.sleep", new_callable=AsyncMock
    ):
        await client.connect_and_monitor()


class TestSuccessfulHandshake:
    async def test_both_sides_derive_the_same_key(self, client):
        """The whole protocol rests on this one value matching."""
        lock = FakeLock(client)
        await run_once(client, lock)

        assert client.shared_key == lock.shared_key

    async def test_the_device_challenge_is_kept(self, client):
        await run_once(client, FakeLock(client))

        assert client.device_challenge == DEVICE_CHALLENGE

    async def test_the_greeting_may_carry_a_two_byte_header(self, client):
        """A 66 byte greeting has a header the client is expected to strip."""
        lock = FakeLock(client, greeting_prefix=b"\x81\x00")
        await run_once(client, lock)

        assert client.device_challenge == DEVICE_CHALLENGE
        assert client.shared_key == lock.shared_key

    async def test_the_reply_is_encrypted_with_the_shared_key(self, client):
        lock = FakeLock(client)
        await run_once(client, lock)

        # The client's frame is [0x81][0x00][length:2][pubkey:32][ciphertext]
        payload = lock.received[0][36:]
        plain = AESCCM(lock.shared_key, tag_length=16).decrypt(
            DEVICE_CHALLENGE[:13], payload, None
        )

        assert plain[:32] == CLIENT_CHALLENGE
        assert plain[32:64].rstrip(b"\x00") == b"admin"

    async def test_the_session_is_marked_as_established(self, client):
        await run_once(client, FakeLock(client))

        assert client.connection_count == 1

    async def test_the_status_is_requested_right_away(self, client):
        """Nothing arrives until the lock is asked, so it is asked at once.

        The frame itself is checked rather than a mock standing in for the
        send. An earlier version of this test patched out the call, which is
        why it stayed green while that very call started waiting for an answer
        the lock does not give and put the monitor in a reconnect loop.
        """
        lock = FakeLock(client)

        await run_once(client, lock)

        # The handshake is the first thing the client sends; the nudge follows.
        nudge = lock.received[-1]
        endpoint = client._decrypt_own_frame(nudge).split("\n", 1)[0]
        assert endpoint == "/api/v1/getStates"


class TestFailedHandshake:
    async def test_a_wrong_proof_leaves_the_client_disconnected(self, client):
        lock = FakeLock(client, authenticate=False)

        async def stop_after_one(_seconds):
            client._monitor_running = False

        def swallow(coro, *args, **kwargs):
            # cancel() is synchronous, so this must not be an AsyncMock
            coro.close()
            return MagicMock()

        with patch(
            "custom_components.winkhaus_doorclient.api.websockets.connect", return_value=lock
        ), patch(
            "custom_components.winkhaus_doorclient.api.os.urandom", return_value=CLIENT_CHALLENGE
        ), patch("asyncio.create_task", side_effect=swallow), patch(
            "asyncio.sleep", side_effect=stop_after_one
        ):
            await client.connect_and_monitor()

        assert client.ws_connected is False
        assert client.connection_count == 0

    async def test_a_wrong_proof_backs_off(self, client, caplog):
        """Reconnecting at once would hammer the lock with handshakes."""
        lock = FakeLock(client, authenticate=False)
        pausen = []

        async def record(seconds):
            pausen.append(seconds)
            client._monitor_running = False

        def swallow(coro, *args, **kwargs):
            # cancel() is synchronous, so this must not be an AsyncMock
            coro.close()
            return MagicMock()

        with patch(
            "custom_components.winkhaus_doorclient.api.websockets.connect", return_value=lock
        ), patch(
            "custom_components.winkhaus_doorclient.api.os.urandom", return_value=CLIENT_CHALLENGE
        ), patch("asyncio.create_task", side_effect=swallow), patch(
            "asyncio.sleep", side_effect=record
        ):
            await client.connect_and_monitor()

        assert pausen == [30]
        assert "Auth Failed" in caplog.text


class TestMalformedAuthReply:
    async def test_a_text_auth_reply_is_rejected(self, client, caplog):
        """The greeting guard has a sibling: the proof must be binary too.

        A text frame here cannot be sliced for the HMAC comparison, so it is
        refused by name instead of failing somewhere further in.
        """
        lock = FakeLock(client)

        async def recv():
            if not lock.received:
                return lock.greeting
            return "this should have been binary"

        lock.recv = recv

        with pytest.raises(ValueError, match="unexpected text frame"):
            await client._perform_handshake(lock)

        assert "expected binary" in caplog.text


class TestWebSocketSslContext:
    """`_ws_ssl_context` decides by the scheme of `ws_uri`.

    Worth knowing while reading this: the integration builds that URI as
    `ws://<ip>:80/ws` and never anything else, so the branch that builds a
    context is not reached in normal operation. The WebSocket is unencrypted
    on the wire - the X25519 exchange and AES-CCM inside it are the only
    protection the channel has, which is presumably why the protocol carries
    them at all. These tests pin the function's contract, not a situation the
    integration currently produces.
    """

    def test_a_plain_ws_uri_needs_no_context(self, client):
        assert client.ws_uri.startswith("ws://")
        assert client._ws_ssl_context() is None

    def test_a_wss_uri_gets_a_context_that_accepts_the_device_certificate(self, client):
        """The locks present a self-signed certificate for an IP address, so
        hostname and chain checks would both fail."""
        client.ws_uri = "wss://10.10.30.197:443/ws"

        ctx = client._ws_ssl_context()

        assert ctx is not None
        assert ctx.check_hostname is False
        assert ctx.verify_mode is ssl.CERT_NONE


class TestConnectionErrors:
    async def test_a_refused_connection_is_retried(self, client, caplog):
        pausen = []

        async def record(seconds):
            pausen.append(seconds)
            client._monitor_running = False

        with patch(
            "custom_components.winkhaus_doorclient.api.websockets.connect",
            side_effect=OSError("connection refused"),
        ), patch("asyncio.sleep", side_effect=record):
            await client.connect_and_monitor()

        assert pausen == [5]
        assert client.ws_connected is False

    async def test_a_silent_lock_does_not_hang_the_handshake(self, client, caplog):
        """A lock may accept the socket and then say nothing at all.

        recv() has no deadline of its own, and the keepalive only closes a
        socket that stops answering pings - a lock whose WebSocket layer is
        alive answers them while its application stays quiet, so the monitor
        task would park on recv() with nothing to end it. The handshake has to
        fail on its own terms and hand the loop back for a retry.
        """

        class SilentLock:
            """Completes the connect, then never speaks."""

            async def recv(self):
                await asyncio.Event().wait()

            async def send(self, data):
                pass

            async def close(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        pausen = []

        async def record(seconds):
            pausen.append(seconds)
            client._monitor_running = False

        with patch(
            "custom_components.winkhaus_doorclient.api.websockets.connect",
            return_value=SilentLock(),
        ), patch(
            "custom_components.winkhaus_doorclient.api.HANDSHAKE_TIMEOUT", 0.01
        ), patch("asyncio.sleep", side_effect=record):
            await client.connect_and_monitor()

        # The same pause an auth failure gets: a silent lock stays silent, so
        # retrying every few seconds only floods it and the log.
        assert pausen == [30]
        assert client.ws_connected is False
        assert client.current_session_start is None
        assert "timed out" in caplog.text.lower()

    async def test_the_session_state_is_cleared_after_an_error(self, client):
        client.current_session_start = 1234.0

        async def stop(_seconds):
            client._monitor_running = False

        with patch(
            "custom_components.winkhaus_doorclient.api.websockets.connect",
            side_effect=OSError("connection refused"),
        ), patch("asyncio.sleep", side_effect=stop):
            await client.connect_and_monitor()

        assert client.current_session_start is None

    async def test_a_dropped_session_pauses_before_reconnecting(self, client):
        """_listen swallows ConnectionClosed, so this pause is the only one."""
        lock = FakeLock(client)
        pausen = []

        async def record(seconds):
            # Stop only once the pause has been observed, since it happens
            # after _listen returns
            pausen.append(seconds)
            client._monitor_running = False

        def swallow(coro, *args, **kwargs):
            # cancel() is synchronous, so this must not be an AsyncMock
            coro.close()
            return MagicMock()

        with patch(
            "custom_components.winkhaus_doorclient.api.websockets.connect", return_value=lock
        ), patch(
            "custom_components.winkhaus_doorclient.api.os.urandom", return_value=CLIENT_CHALLENGE
        ), patch("asyncio.create_task", side_effect=swallow), patch(
            "asyncio.sleep", side_effect=record
        ):
            await client.connect_and_monitor()

        assert pausen == [5]


class TestVerifyCredentials:
    """The check the config flow runs before it creates an entry.

    HTTP authenticates with basic auth and never sees the serial number, so
    a wrong one used to pass setup and surface only as a WebSocket that
    never authenticated. This is where it gets caught - but only a refusal
    counts as evidence.
    """

    async def test_an_accepted_handshake_confirms_the_credentials(self, client) -> None:
        lock = FakeLock(client, authenticate=True)

        with patch(
            "custom_components.winkhaus_doorclient.api.websockets.connect",
            return_value=lock,
        ), patch(
            "custom_components.winkhaus_doorclient.api.os.urandom",
            return_value=CLIENT_CHALLENGE,
        ):
            assert await client.verify_credentials() is True

    async def test_a_refused_handshake_is_a_definite_no(self, client) -> None:
        lock = FakeLock(client, authenticate=False)

        with patch(
            "custom_components.winkhaus_doorclient.api.websockets.connect",
            return_value=lock,
        ), patch(
            "custom_components.winkhaus_doorclient.api.os.urandom",
            return_value=CLIENT_CHALLENGE,
        ):
            assert await client.verify_credentials() is False

    async def test_an_unreachable_socket_says_nothing(self, client) -> None:
        """A blocked WebSocket must not be read as a wrong serial number."""
        with patch(
            "custom_components.winkhaus_doorclient.api.websockets.connect",
            side_effect=OSError("no route to host"),
        ):
            assert await client.verify_credentials() is None

    async def test_a_timeout_says_nothing_either(self, client) -> None:
        class NeverConnects:
            async def __aenter__(self):
                await asyncio.sleep(3600)

            async def __aexit__(self, *args):
                return False

        with patch(
            "custom_components.winkhaus_doorclient.api.websockets.connect",
            return_value=NeverConnects(),
        ):
            assert await client.verify_credentials(timeout=0.05) is None

    async def test_a_malformed_greeting_says_nothing(self, client) -> None:
        """Garbage on the wire is not evidence about the credentials."""
        lock = FakeLock(client)
        lock.greeting = "this should have been binary"

        with patch(
            "custom_components.winkhaus_doorclient.api.websockets.connect",
            return_value=lock,
        ):
            assert await client.verify_credentials() is None
