# in custom_components/winkhaus_doorclient/api.py

import logging
import ssl
import asyncio
import aiohttp
import websockets
import json
import struct
import os
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

# Cryptography
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.hmac import HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

_LOGGER = logging.getLogger(__name__)

WINKHAUS_STATUS_MAP = {
    "state": ["open", "closed"],
    "mode": ["day", "night"]
}

# Mapping from Home Assistant intent to the lock's own command vocabulary.
# The device itself only understands: "day", "night", "unlock".
# Note: "open" maps to "unlock" because on this device "unlock" pulls the latch.
COMMAND_MAP = {
    "lock":   "night",
    "unlock": "day",
    "night":  "night",
    "day":    "day",
    "open":   "unlock",
}

VALID_MODES = ("day", "night")

# The lock needs a moment after setup before it accepts a WebSocket.
WS_START_DELAY = 2.0

# Upper bound for the handshake. Its two recv() calls have no deadline of
# their own, so a lock that accepts the connection and then stays silent would
# park the monitor task on recv() indefinitely: the keepalive only closes a
# socket that stops answering pings, and a lock whose WebSocket layer is alive
# answers them while its application says nothing. verify_credentials already
# bounds the same exchange at 10 s for the config flow; this brings the
# monitor in line. The doorControl app allows 3 s per handshake step.
HANDSHAKE_TIMEOUT = 10.0

# Packet type nibbles the lock uses on the wire. A reply to a request and an
# unsolicited state push are told apart by this, not by their shape: both can
# carry a filled XC_SUC object. Measured on a blueMotion+, firmware 1.6.2.
PACKET_TYPE_PUSH = 1
PACKET_TYPE_REPLY = 6

# Upper bound for one request over the WebSocket. The slowest answer measured
# was /api/v1/scan at over five seconds; everything else came back in 100 to
# 300 ms. Ten seconds leaves room for a slower endpoint without letting a lost
# answer hold a caller indefinitely.
WS_REQUEST_TIMEOUT = 10.0

# Watchdog thresholds. These are tuned against the keepalive rather than
# picked, and the reasoning matters because ROADMAP.md refuses to make them
# configurable on the grounds that they interlock.
#
# The keepalive does the fast work: ping_interval 20 plus ping_timeout 10
# means the library closes a socket that stops answering pings within thirty
# seconds, and it does so without any traffic of ours. What it cannot see is a
# lock that answers pings while its application has gone quiet - that is the
# only case left for the watchdog, and it is not an urgent one. Entities keep
# their last reading during an outage, as the README's known limitations say,
# so nothing wrong is displayed in the meantime.
#
# Protocol pings never reach `last_message_time`: it is only set inside the
# listener's message loop, and the library handles ping and pong below that.
# A healthy but silent connection therefore looks idle to the watchdog, which
# is why a short threshold made it manufacture the traffic it then observed -
# a nudge every 75 s, 1152 requests a day, detecting nothing.
WATCHDOG_SILENCE_THRESHOLD = 300
WATCHDOG_FALLBACK_THRESHOLD = 310


class DeviceSilentError(TimeoutError):
    """The request went out and no answer came back.

    Distinct from a failure to send, and the distinction decides whether the
    request may be repeated. The frame reached the wire, so whether the lock
    acted on it is unknown - and for a command that uncertainty is not a
    reason to send it again by another route. `unlock` pulls the latch, so a
    second route would be a second actuation. Reads carry no side effect and
    may fall back freely.

    Subclasses TimeoutError so a caller that only cares that nothing came
    back can still catch that.
    """


class DeviceRefusedError(Exception):
    """The lock answered and refused the request.

    Separate from a transport failure on purpose: a refusal must not be
    retried over HTTP. The lock received the request and said no, so sending
    it a second time by another route would either change nothing or, for a
    command, carry it out twice.
    """

def describe_device_error(err: Any, fallback: str) -> str:
    """Name the cause of an XC_ERR, from whichever field carries it.

    A refusal has two fields with different jobs. "code" is the category -
    API_ERROR for a factual error such as an unknown endpoint or a missing
    parameter, AUTH_ERROR for a rejected authorisation - and "text" is the
    detail the lock adds when it has something more specific to say. They
    are not alternatives: both arrive together where the lock knows more.

    Both are also optional, and either can arrive alone. Measured:

        getLogs without ?type=   {"XC_ERR":{"code":"API_ERROR"}}
        external API switched off {"XC_ERR":{"text":"api unavailable"}}

    So "text" first, "code" as the fallback. That order is not a preference
    but a requirement: the second shape above is the most common failure
    this integration sees, and anything that reads "code" first and stops
    there has nothing to tell the user about it. The original app reads only
    "text" and shows nothing at all for the first shape.

    A field the lock actually sent is reported verbatim, a zero and an empty
    string included, because even a bare code is something to look up while a
    placeholder is nothing. Only a field that is absent, null or empty falls
    through to the next one, and the fallback is reached when neither field
    carries anything - a shape that has shown up once in seven weeks of
    continuous use and has never been explained.

    A refusal that is not an object is stringified. The WebSocket listener has
    always handled that shape; the HTTP path used to call .get() on it and
    raise AttributeError where no handler covers it.
    """
    if not isinstance(err, dict):
        return str(err)
    for key in ("text", "code"):
        if key in err and err[key] is not None and err[key] != "":
            return str(err[key])
    return fallback


def create_legacy_ssl_context() -> ssl.SSLContext:
    """Build an SSL context the door controller will accept.

    The embedded firmware offers ciphers and a renegotiation style that
    modern defaults refuse, so the security level is lowered and legacy
    renegotiation is allowed. Certificate checks are off because the device
    presents a self-signed certificate for an IP address.

    Loading the default trust store touches the file system, so this must
    run in an executor rather than on the event loop.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_ciphers("DEFAULT:@SECLEVEL=1")
    context.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
    return context

class DoorClient:
    def __init__(
        self,
        serial_number: str,
        ip: str,
        password: str,
        session: aiohttp.ClientSession,
        ssl_context: ssl.SSLContext,
        port: int = 443,
        username: str = "admin",
    ):
        self.serial_number = serial_number
        self.ip = ip
        self.port = port
        self.username = username
        self._password = password
        self._timeout = aiohttp.ClientTimeout(total=15)

        # Session and SSL context are supplied by the caller. Home Assistant
        # owns the session, so this class must never close it.
        self._session = session
        self._ssl = ssl_context
        
        # WebSocket Setup
        self.ws_port = 80
        self.ws_uri = f"ws://{self.ip}:{self.ws_port}/ws"
        self.ws_connected = False
        # The websockets library exposes no stable public type for the
        # connection object, so this stays deliberately untyped.
        self._active_ws: Any = None
        
        # Crypto & State
        self.shared_key: bytes | None = None
        self.device_challenge: bytes | None = None
        self.client_challenge: bytes | None = None
        self.client_counter = 0
        self.last_message_time = 0.0
        
        # Consecutive failed fallback fetches, and whether the loss was
        # already reported. The nudge going unanswered is one probe and the
        # failed fetch a second, but a single HTTP hiccup at the wrong moment
        # must not grey out the dashboard - so the report waits for the
        # second failure, and then comes once per outage rather than every
        # ten seconds for an hour.
        self._fallback_failures = 0
        self._contact_loss_reported = False

        # Callbacks & Tasks
        self.on_state_change: Callable[[list[dict[str, Any]]], None] | None = None
        # Raised when neither route reaches the lock any more. The client
        # reports and decides nothing: whether the shown state has to go is
        # the coordinator's rule, because only it knows how old that state is.
        self.on_contact_lost: Callable[[], None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[Any] | None = None
        self._monitor_running = False
        # Set by stop() and never cleared. An entry on its way out must not
        # be able to start a monitor that had not begun yet.
        self._stop_requested = False
        
        # --- SIMPLE CONNECTION TRACKING ---
        self.connection_count = 0  # Total number of connections made
        self.current_session_start: float | None = None  # Timestamp of current session start
        self.last_session_seconds = 0.0  # Duration of the session that just ended
        # ------------------------------------

        # --- FRAGMENT REASSEMBLY ---
        # One buffer per packet type: the lock interleaves unsolicited state
        # pushes with the fragments of a reply, so a shared buffer would let a
        # push truncate the reply. _listen carries the measurement this rests on.
        self._rx_buffers: dict[int, bytearray] = {}
        # ----------------------------

        # --- REQUEST ATTRIBUTION ---
        # The device never echoes our counter back, so a rejection cannot be
        # matched to its request by ID. The original client has the same
        # limitation and simply remembers the most recent request, which is
        # accurate as long as commands are not pipelined.
        self._last_request: tuple[str, dict[str, Any] | None, float] | None = None
        # ----------------------------

        # --- REPLAY PROTECTION ---
        # The device runs its own counter, independent of ours. Accepting only
        # strictly increasing values rejects replays and stale frames. None
        # means "no message seen yet", so the first one is always accepted.
        self._device_counter: int | None = None
        # ----------------------------

        # --- REQUEST/RESPONSE OVER THE WEBSOCKET ---
        # The lock answers strictly in order and carries no request
        # identifier, so the order is the only correlation there is. One
        # request at a time keeps it unambiguous: the lock serialises its own
        # work anyway, so pipelining would buy nothing measurable, while a
        # single lost answer would misalign every following one with no way
        # back except a new handshake.
        self._ws_lock = asyncio.Lock()
        self._ws_reply: asyncio.Future[str] | None = None
        # ----------------------------

    # --- SIMPLE TRACKING METHODS ---
    def get_current_uptime(self) -> float:
        """Get current session uptime in seconds."""
        if self.current_session_start:
            return time.time() - self.current_session_start
        return 0.0

    def _format_session_duration(self) -> str:
        """Human readable length of the session that just ended."""
        total = int(self.last_session_seconds)
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _describe_last_request(self) -> str:
        """Name the request a rejection most likely belongs to.

        Attribution is by recency, not by ID, because the device does not
        echo our counter. That is reliable while a single request is in
        flight, which is the normal case here. If the last request went out
        a while ago the match is doubtful, so the age is spelled out rather
        than presented as fact.
        """
        if not self._last_request:
            return "a request (none recorded)"

        endpoint, payload, sent_at = self._last_request
        age = time.time() - sent_at
        described = f"{endpoint} {payload}" if payload else endpoint

        if age > 10:
            return f"a request - last one was {described} {age:.0f}s ago, may be unrelated"
        return f"{described} (sent {age:.1f}s ago)"
    # --------------------------------

    # --- CRYPTO HELPERS ---
    def _get_pbdf2_key(self) -> bytes:
        salt = (self.serial_number + ":" + self.username).encode('utf-8')
        kdf = PBKDF2HMAC(hashes.SHA256(), 32, salt, 1000, default_backend())
        return kdf.derive(self._password.encode('utf-8'))

    def _create_hmac_sha1(self, key: bytes, data: bytes) -> bytes:
        h = HMAC(key, hashes.SHA1(), default_backend())
        h.update(data)
        return h.finalize()

    def _get_iv(self, base_challenge: bytes, counter: int) -> bytes:
        iv = bytearray(base_challenge[:13])
        iv[9:13] = counter.to_bytes(4, 'big')
        return bytes(iv)

    # --- HTTP METHODS (Synchronous Fallback) ---
    async def connect(self) -> bool:
        """Whether the lock answers. Reports failure without raising,
        because the config flow decides what to show the user."""
        try:
            await self.get_states()
            return True
        except Exception as err:
            _LOGGER.error(f"HTTP Connection failed: {err}")
            return False

    async def _request(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"https://{self.ip}:{self.port}{path}"
        method = "POST" if data else "GET"

        try:
            async with self._session.request(
                method,
                url,
                json=data,
                auth=aiohttp.BasicAuth(self.username, self._password),
                ssl=self._ssl,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                body = await response.read()

                if not body:
                    return {}

                # The firmware does not always announce a JSON content type,
                # so the check is disabled rather than trusted.
                response_json = json.loads(body)

        except aiohttp.ClientResponseError:
            # Carries the status code the config flow needs to tell a wrong
            # password from an unreachable device.
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise Exception(f"Network error: {err}") from err

        if "XC_ERR" in response_json:
            error_msg = describe_device_error(
                response_json["XC_ERR"], "Unknown API error"
            )
            raise Exception(f"Device Error: {error_msg}")

        result: dict[str, Any] = response_json.get("XC_SUC", {})
        return result

    async def get_states(self) -> list[dict[str, Any]]:
        """Read the current state, over the WebSocket when one is up.

        Measured on a blueMotion+: the answer is a reply-typed frame like any
        other read, and it carries the same four fields the HTTP answer does -
        state, locked, mode and time. The push that arrives on its own carries
        the first three and leaves out time, which is why a push is merged
        into the last full reading rather than replacing it. That difference
        is a property of the push, not of the transport, so it is unaffected
        by where the full reading comes from.
        """
        return self._format_states(await self._read("/api/v1/getStates"))

    async def get_states_http(self) -> list[dict[str, Any]]:
        """Read the state over HTTP, deliberately bypassing the WebSocket.

        For the two callers whose purpose is the other transport: the
        watchdog's fallback fetch, which runs because the socket has gone
        quiet, and the refresh after a command that already had to go over
        HTTP. Going through the WebSocket first would spend the request
        timeout on a connection both of them already suspect.
        """
        return self._format_states(await self._request("/api/v1/getStates"))

    def _format_states(self, raw_states: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_states, dict):
            return []
            
        interpreted_states = []
        for key, raw_value in raw_states.items():
            logical_value = raw_value
            if key in WINKHAUS_STATUS_MAP and isinstance(raw_value, int):
                try:
                    logical_value = WINKHAUS_STATUS_MAP[key][raw_value]
                except IndexError:
                    pass
            interpreted_states.append({"name": key, "value": logical_value})
        return interpreted_states

    async def _read(self, endpoint: str) -> dict[str, Any]:
        """Read an endpoint over the WebSocket, over HTTP when it is not up.

        The lock serves these over either transport. The WebSocket is
        preferred because it is the connection the integration already holds
        open, so a read there costs no new handshake and no new TCP session.

        A refusal is passed on rather than retried: the lock received the
        request and answered it, so asking again over HTTP would get the same
        answer. Anything else is transport trouble, and HTTP is a different
        transport - that is what makes it a useful fallback rather than a
        second guess.
        """
        if self.ws_connected and self._active_ws:
            try:
                return await self.async_ws_request(endpoint)
            except DeviceRefusedError as err:
                raise Exception(f"Device Error: {err}") from err
            except Exception as err:
                _LOGGER.debug(
                    f"[{self.serial_number}] WS read of {endpoint} failed "
                    f"({err}). Falling back to HTTP."
                )

        return await self._request(endpoint)

    async def get_system_state(self) -> dict[str, Any]:
        return await self._read("/api/v1/getSystemState")

    async def get_configuration(self) -> dict[str, Any]:
        return await self._read("/api/v1/getConfiguration")

    def _encode_ws_request(self, endpoint: str, payload: dict[str, Any] | None) -> bytes:
        """Build one request frame and advance the counter.

        The counter goes into the nonce, so every frame needs its own and no
        value may be reused within a session.
        """
        if self.shared_key is None or self.client_challenge is None:
            raise RuntimeError("Send attempted before the handshake completed")

        self.client_counter += 1
        ws_data = f"{endpoint}\n{json.dumps(payload) if payload else '{}'}".encode('utf-8')
        iv = self._get_iv(self.client_challenge, self.client_counter)
        encrypted = AESCCM(self.shared_key, tag_length=16).encrypt(iv, ws_data, None)
        header = b'\x85\x00' + len(encrypted).to_bytes(2, 'big')
        return header + self.client_counter.to_bytes(4, 'big') + encrypted

    def _decrypt_own_frame(self, frame: bytes) -> str:
        """Undo _encode_ws_request. For diagnostics and for tests that want to
        see what actually went on the wire rather than trust a mock."""
        if self.shared_key is None or self.client_challenge is None:
            raise RuntimeError("No key material to decrypt with")

        counter = int.from_bytes(frame[4:8], 'big')
        iv = self._get_iv(self.client_challenge, counter)
        plain: bytes = AESCCM(self.shared_key, tag_length=16).decrypt(iv, frame[8:], None)
        return plain.decode('utf-8')

    async def _drop_socket(self) -> None:
        """Close the live socket so the monitor loop rebuilds the session."""
        ws = self._active_ws
        if ws is None:
            return
        try:
            await ws.close()
        except Exception as err:
            _LOGGER.debug(f"[{self.serial_number}] Closing the socket failed: {err}")

    async def _send_status_nudge(self) -> None:
        """Ask the lock to report its state, without waiting for an answer.

        This is a nudge, not a question, and the difference is measured rather
        than assumed. Put through the correlation layer on 15.11.2026 it never
        saw a reply-typed frame and timed out every single time: the socket
        was dropped, the monitor rebuilt it, and the whole cycle repeated
        every sixteen seconds. `getStates` is not answered the way
        `getGroups` or `getSystemState` are: it comes back as a single
        fragment of 75 bytes, carrying `state` and `mode`.

        Whatever the lock sends back therefore arrives as a state message and
        is wanted exactly there: the dispatch in `_listen` publishes it, which
        is how this has worked since the hybrid transport landed.

        Both callers fire this only when nothing else is in flight - right
        after a handshake, and from the watchdog after 75 s of silence - so
        not occupying a slot in the order cannot mislead another request.
        """
        ws = self._active_ws
        if not self.ws_connected or ws is None:
            return

        try:
            async with self._ws_lock:
                await ws.send(self._encode_ws_request("/api/v1/getStates", {}))
            _LOGGER.debug(f"[{self.serial_number}] Status nudge sent.")
        except Exception as err:
            # Nothing to recover: the coordinator polls anyway, and a socket
            # this broken is the watchdog's business, not the nudge's.
            _LOGGER.debug(f"[{self.serial_number}] Status nudge failed: {err}")

    async def async_ws_request(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a request over the WebSocket and return what the lock answered.

        One request is in flight at a time. The lock has no request
        identifier, so the answer is identified by being the next reply-typed
        message to arrive - which only holds while nothing else is waiting.

        Raises `DeviceRefusedError` when the lock answered and refused, which
        must not be retried elsewhere. Raises `TimeoutError` when no answer
        arrived, and in that case drops the socket: a late answer would be
        handed to the next request, and the order is all we have to go on.
        Raises `RuntimeError` when the socket is not usable at all.
        """
        # Resolved here rather than as a default argument: a default is bound
        # when the function is defined, so a test that moves the constant to
        # keep itself quick would not actually change what this waits for.
        if timeout is None:
            timeout = WS_REQUEST_TIMEOUT

        ws = self._active_ws
        if not self.ws_connected or ws is None:
            raise RuntimeError("WebSocket not connected")

        async with self._ws_lock:
            reply: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            self._ws_reply = reply
            try:
                await ws.send(self._encode_ws_request(endpoint, payload))

                # Remember what went out last. A rejection that arrives
                # outside a request - for a command we did not wait on, say -
                # has no other way to be named.
                self._last_request = (endpoint, payload, time.time())

                try:
                    async with asyncio.timeout(timeout):
                        raw = await reply
                except TimeoutError:
                    _LOGGER.warning(
                        f"[{self.serial_number}] No answer to {endpoint} within "
                        f"{timeout:.0f}s. Dropping the socket to resynchronise, "
                        f"because a late answer would be read as the next one."
                    )
                    await self._drop_socket()
                    raise DeviceSilentError(
                        f"no answer to {endpoint} within {timeout:.0f}s"
                    ) from None
            finally:
                self._ws_reply = None

        data = json.loads(raw)
        if "XC_ERR" in data:
            raise DeviceRefusedError(
                describe_device_error(data["XC_ERR"], "Unknown API error")
            )

        result: dict[str, Any] = data.get("XC_SUC", {})
        _LOGGER.debug(f"WS request to {endpoint} answered: {result}")
        return result

    # --- ASYNC COMMAND & HYBRID LOGIC ---
    async def async_send_payload(self, endpoint: str, payload: dict[str, Any] | None = None) -> bool:
        
        if self.ws_connected and self._active_ws:
            if self.shared_key is None or self.client_challenge is None:
                raise RuntimeError("Send attempted before the handshake completed")
            try:
                await self.async_ws_request(endpoint, payload)
                _LOGGER.debug(f"Command sent via WS to {endpoint}: {payload}")
                return True
            except DeviceRefusedError as err:
                # The lock received the command and refused it. Reporting that
                # to the caller would surface an error where none was shown
                # before, and no benign refusal code is known yet - so it is
                # recorded and the result stays what it has always been. The
                # log is how we find out which codes exist.
                _LOGGER.warning(
                    f"[{self.serial_number}] Lock rejected "
                    f"{self._describe_last_request()}: {err}"
                )
                return True
            except DeviceSilentError:
                # The frame did go out. Whether the lock carried it out is
                # unknown, and that uncertainty is not a reason to send it a
                # second time by another route: `unlock` pulls the latch, so
                # the retry would be a second actuation. Reported as sent,
                # which is what the result has always meant here.
                # Named directly rather than through _describe_last_request:
                # that one hedges when the last request is more than ten
                # seconds old, which after a ten second timeout it always is -
                # so the hedge would fire every time while we know exactly
                # which request this was.
                _LOGGER.warning(
                    f"[{self.serial_number}] No acknowledgement for {endpoint} "
                    f"{payload if payload else ''}. The command was sent; "
                    f"whether the lock acted on it is unknown, so it is not "
                    f"repeated over HTTP."
                )
                return True
            except Exception as e:
                # The frame did not get out, so nothing has happened at the
                # lock yet and another transport may still reach it.
                _LOGGER.warning(f"WS send failed ({e}), falling back to HTTP.")

        try:
            await self._request(endpoint, payload)
            _LOGGER.debug(f"Command sent via HTTP to {endpoint}: {payload}")
            return True
        except Exception as e:
            _LOGGER.error(f"Send failed (WS & HTTP): {e}")
            return False

    async def async_execute_command(self, command: str, value: str | None = None) -> bool:
        # Resolve the Home Assistant intent to a command the lock understands.
        # Reject anything unknown instead of sending an invalid payload.
        if command == "mode":
            if value not in VALID_MODES:
                _LOGGER.error(
                    f"[{self.serial_number}] Invalid mode '{value}'. "
                    f"Expected one of {VALID_MODES}. Command ignored."
                )
                return False
            device_command: str = value
        else:
            resolved = COMMAND_MAP.get(command)
            if resolved is None:
                _LOGGER.error(
                    f"[{self.serial_number}] Unknown command '{command}'. "
                    f"Expected one of {tuple(COMMAND_MAP)}. Command ignored."
                )
                return False
            device_command = resolved

        success = await self.async_send_payload(
            "/api/v1/control", {"command": device_command}
        )

        if success and not self.ws_connected:
            _LOGGER.info(f"Command '{command}' sent via HTTP. Simulating push update...")
            await asyncio.sleep(3)
            fallback_data = await self.get_states_http()
            if fallback_data and self.on_state_change:
                self.on_state_change(fallback_data)
                
        return success
        
    async def async_unblock(self) -> bool:
        success = await self.async_send_payload("/api/v1/unblock", {})
        if success:
            _LOGGER.info(f"Unblock command successfully sent to {self.serial_number}.")
        else:
            _LOGGER.error(f"Failed to send unblock command to {self.serial_number}.")
        return success

    # --- WEBSOCKET LISTENER & WATCHDOG ---
    async def _watchdog_loop(self) -> None:
        _LOGGER.info(
            f"Watchdog started ({WATCHDOG_SILENCE_THRESHOLD}s trigger interval)."
        )
        while not self._stop_requested:
            await asyncio.sleep(5)
            time_since_last = time.time() - self.last_message_time

            if (
                time_since_last > WATCHDOG_SILENCE_THRESHOLD
                and self.ws_connected
                and self._active_ws
            ):
                _LOGGER.debug(f"Watchdog: {int(time_since_last)}s no message. Pinging...")
                # No handler around these two: the nudge reports its own
                # failures and raises nothing, so a handler here could only
                # ever catch the sleep - and a cancelled sleep is how this
                # loop is meant to end.
                await self._send_status_nudge()
                await asyncio.sleep(5)

                if time.time() - self.last_message_time > WATCHDOG_FALLBACK_THRESHOLD:
                    _LOGGER.warning("WS unresponsive. Triggering HTTP Fallback fetch.")
                    try:
                        fallback_data = await self.get_states_http()
                        if fallback_data and self.on_state_change:
                            self.on_state_change(fallback_data)
                        self.last_message_time = time.time()
                        self._fallback_failures = 0
                        self._contact_loss_reported = False
                    except Exception as e:
                        _LOGGER.error(f"HTTP Fallback fetch failed: {e}")
                        self._fallback_failures += 1
                        if (
                            self._fallback_failures >= 2
                            and not self._contact_loss_reported
                        ):
                            self._contact_loss_reported = True
                            _LOGGER.warning(
                                f"[{self.serial_number}] Neither the socket nor "
                                f"HTTP is reaching the lock. Reporting the loss."
                            )
                            if self.on_contact_lost:
                                self.on_contact_lost()

    async def _listen(self, websocket: Any) -> None:
        # Only ever called after a successful handshake, which is what sets
        # these. Saying so explicitly keeps the decrypt path honest.
        if self.shared_key is None or self.device_challenge is None:
            raise RuntimeError("Listener started before the handshake completed")

        _LOGGER.info("WS Listener ready.")
        self.last_message_time = time.time()
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        
        try:
            async for message in websocket:
                if len(message) < 8:
                    continue
                
                # Only real messages count as a sign of life
                self.last_message_time = time.time()
                
                try:
                    header = message[0]
                    is_final = bool(header & 0x80)
                    packet_type = header & 0x0F

                    counter = int.from_bytes(message[4:8], 'big')

                    # Reject replays and stale frames. The counter is only
                    # advanced after a successful decrypt, so a corrupted frame
                    # cannot lock out the messages that follow it.
                    if self._device_counter is not None and counter <= self._device_counter:
                        _LOGGER.debug(
                            f"Ignoring message with non-increasing counter "
                            f"{counter} (last was {self._device_counter})."
                        )
                        continue

                    iv = self._get_iv(self.device_challenge, counter)
                    decrypted = AESCCM(self.shared_key, tag_length=16).decrypt(iv, message[8:], None)
                    self._device_counter = counter

                    # The device splits a long answer into fragments and
                    # only sets the 0x80 bit on the last one. Every fragment
                    # is encrypted separately with its own counter, so decrypt
                    # first and concatenate the plaintext.
                    #
                    # Fragment sizes are not a size limit the device honours:
                    # it writes along its handler's own write calls, and the
                    # largest fragment measured was 663 bytes. The 1024 byte
                    # rule in the vendor library belongs to the opposite
                    # direction, where a client splits a long request. Nothing
                    # here may assume a fragment size - only that the last one
                    # is marked.
                    #
                    # Each packet type is collected on its own buffer because
                    # the lock interleaves unsolicited state pushes (type 1)
                    # with the fragments of a reply (type 6).
                    #
                    # Measured on a blueMotion+ (firmware 1.6.2) on 2026-11-15:
                    # a 218 s capture recorded 617 frames - 70 replies at ~8.4
                    # fragments each, plus 28 state pushes - and caught two of
                    # those pushes arriving while a reply was still being
                    # reassembled, with 632 and 17 bytes already buffered. An
                    # 8-fragment reply streams over roughly 220 ms, so a push
                    # lands inside one about 7% of the time, which matches the
                    # two hits seen across 28 pushes.
                    #
                    # Sharing one buffer across types discards the
                    # half-received reply, which then fails to decode.
                    buffer = self._rx_buffers.setdefault(packet_type, bytearray())
                    buffer.extend(decrypted)

                    if not is_final:
                        # A lost final fragment would otherwise grow this
                        # buffer without bound on a long-lived connection.
                        if len(buffer) > 65536:
                            _LOGGER.warning(
                                f"[{self.serial_number}] Reassembly buffer for packet type "
                                f"{packet_type} exceeded 64 KB without a final fragment. "
                                f"Discarding."
                            )
                            del self._rx_buffers[packet_type]
                        continue

                    # Decode only once the message is complete: a fragment can
                    # end in the middle of a multi-byte UTF-8 character.
                    #
                    # The buffer is retired BEFORE decoding. A message the lock
                    # sent with invalid UTF-8 is lost either way, but leaving
                    # its bytes behind made every later message of the same
                    # packet type fail on them too, until the connection was
                    # rebuilt. Losing one message is a hiccup; losing the
                    # packet type for the rest of the session is an outage.
                    raw = bytes(buffer)
                    del self._rx_buffers[packet_type]
                    payload = raw.decode('utf-8')

                    # A reply belongs to whoever is waiting for one. Only the
                    # packet type says so - a reply and a push can both carry
                    # a filled XC_SUC, so the shape cannot decide it. With
                    # nobody waiting the message falls through to the dispatch
                    # below, which is what happens to the answers of the
                    # status nudges and to every unsolicited push.
                    waiting = self._ws_reply
                    if (
                        packet_type == PACKET_TYPE_REPLY
                        and waiting is not None
                        and not waiting.done()
                    ):
                        waiting.set_result(payload)
                        continue

                    if payload.strip().startswith('{'):
                        data = json.loads(payload)
                        target = data.get("XC_SUC", data)
                        
                        if "state" in target or "mode" in target:
                            formatted_info = self._format_states(target)
                            _LOGGER.debug(f"WS Status Update: {formatted_info}")
                            if self.on_state_change:
                                self.on_state_change(formatted_info)
                        elif "XC_ERR" in data:
                            # XC_ERR means the lock REJECTED a request. Named
                            # by the same helper the HTTP path uses, so the two
                            # transports can never word a refusal differently.
                            err_text = describe_device_error(
                                data.get("XC_ERR"), "Unknown error"
                            )
                            _LOGGER.warning(
                                f"[{self.serial_number}] Lock rejected "
                                f"{self._describe_last_request()}: {err_text}"
                            )
                        elif "XC_SUC" in data and not target:
                            _LOGGER.debug("[WS ACK] Command successfully acknowledged by lock.")
                        # ------------------------------------------------------------------
                        else:
                            _LOGGER.debug(f"[WS FILTER] Unknown message ignored: {data}")

                except Exception as e:
                    _LOGGER.error(f"WS Decode Error: {e}")

        except websockets.exceptions.ConnectionClosed as e:
            _LOGGER.warning(f"WS Connection closed: {e}")
        finally:
            # No matter how we get here (exception OR normal end of the loop):
            # the socket is dead. ALWAYS reset the state, otherwise
            # async_send_payload keeps trying to send over the WebSocket.
            # Keep the duration first - it is the most useful piece of
            # information when a session drops unexpectedly.
            self.last_session_seconds = self.get_current_uptime()
            self.ws_connected = False
            self._active_ws = None
            self.current_session_start = None

            # Do not carry a half-assembled message into the next session
            self._rx_buffers.clear()

            # The device restarts its counter on every handshake, so a stale
            # value here would reject every message of the next session.
            self._device_counter = None
            
            # Not awaited here: this runs inside the monitor task and the
            # watchdog is rebuilt on the next handshake anyway. The observed
            # teardown that has to finish belongs to stop().
            if self._watchdog_task:
                self._watchdog_task.cancel()

    async def _cancel_watchdog(self) -> None:
        """Stop the watchdog and wait for it, so the task is really gone."""
        task = self._watchdog_task
        self._watchdog_task = None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _cancel_monitor(self) -> None:
        """Stop the reconnect loop and wait for it to unwind."""
        task = self._monitor_task
        self._monitor_task = None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _perform_handshake(self, ws: Any) -> bool:
        """Run the X25519 exchange and the challenge response on an open socket.

        True when the lock accepted the key material. False when it answered
        and the response did not verify, which means the password or the
        serial number is wrong - the serial is part of the PBKDF2 salt, so at
        this level a typo in it is indistinguishable from a bad password.
        """
        msg = await ws.recv()
        if isinstance(msg, str):
            _LOGGER.error("WS greeting arrived as text, expected binary.")
            raise ValueError("unexpected text frame during handshake")

        # A 66 byte greeting carries a two byte header
        srv: bytes = msg[2:] if len(msg) == 66 else msg
        self.device_challenge = srv[32:]
        priv = x25519.X25519PrivateKey.generate()
        pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.shared_key = priv.exchange(x25519.X25519PublicKey.from_public_bytes(srv[:32]))
        
        pwd_key = self._get_pbdf2_key()
        self.client_challenge = os.urandom(32)
        hmac_val = self._create_hmac_sha1(pwd_key, self.device_challenge + self.client_challenge)
        user = self.username.encode('utf-8').ljust(32, b'\x00')
        enc = AESCCM(self.shared_key, tag_length=16).encrypt(self.device_challenge[:13], self.client_challenge + user + hmac_val, None)
        
        await ws.send(b'\x81\x00' + struct.pack('>H', len(pub)+len(enc)) + pub + enc)
        resp = await ws.recv()
        if isinstance(resp, str):
            _LOGGER.error("WS auth reply arrived as text, expected binary.")
            raise ValueError("unexpected text frame during handshake")

        check = resp[4:] if len(resp) == 24 else resp
        return bool(check == self._create_hmac_sha1(pwd_key, self.client_challenge))

    def _ws_ssl_context(self) -> ssl.SSLContext | None:
        if not self.ws_uri.startswith("wss"):
            return None
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def verify_credentials(self, timeout: float = 10.0) -> bool | None:
        """Whether the lock accepts our key material. For the config flow.

        True when the handshake succeeded. False when the lock answered and
        rejected us. None when the WebSocket could not be reached or spoke
        unexpectedly - that says nothing about the credentials, and an
        installation whose WebSocket is blocked has to stay setup-able in
        polling mode.
        """
        try:
            async with asyncio.timeout(timeout):
                async with websockets.connect(
                    self.ws_uri,
                    ssl=self._ws_ssl_context(),
                    ping_interval=None,
                    close_timeout=5,
                ) as ws:
                    try:
                        return await self._perform_handshake(ws)
                    except Exception as err:
                        _LOGGER.debug(
                            f"[{self.serial_number}] Handshake check inconclusive: {err}"
                        )
                        return None
        except Exception as err:
            _LOGGER.debug(
                f"[{self.serial_number}] WebSocket not reachable for the "
                f"handshake check: {err}"
            )
            return None

    async def stop(self) -> None:
        # Set before anything is cancelled: a monitor still inside its start
        # delay reads this and returns without ever opening a socket.
        self._stop_requested = True
        self._monitor_running = False

        await self._cancel_watchdog()
        await self._cancel_monitor()

        if self._active_ws:
            try:
                await self._active_ws.close()
            except Exception:
                pass
        self.ws_connected = False
        self._active_ws = None
        # --- Track disconnect ---
        self.current_session_start = None

        # The session belongs to Home Assistant, so it is not closed here.

        _LOGGER.debug("DoorClient background tasks stopped successfully.")
        
    async def connect_and_monitor(self, start_delay: float = 0.0) -> None:
        # Home Assistant schedules this as a background task, so the task
        # object only exists from here on. Keeping it is what gives stop()
        # something to cancel.
        self._monitor_task = asyncio.current_task()
        try:
            await self._monitor_loop(start_delay)
        finally:
            self._monitor_running = False
            self._monitor_task = None

    async def _monitor_loop(self, start_delay: float) -> None:
        if start_delay:
            await asyncio.sleep(start_delay)

        # An unload during the start delay has already set this. Without the
        # check the loop below would set _monitor_running back to True and
        # connect on behalf of an entry that is already gone.
        if self._stop_requested:
            _LOGGER.debug(
                f"[{self.serial_number}] Monitor not started: stop was requested."
            )
            return

        self._monitor_running = True
        while self._monitor_running:
            ssl_ctx = self._ws_ssl_context()

            try:
                _LOGGER.info(f"Connecting WS to {self.ws_uri}...")
                async with websockets.connect(self.ws_uri, ssl=ssl_ctx, ping_interval=20, ping_timeout=10, close_timeout=5) as ws:
                    # A silent lock must not hold this task on recv(). Failing
                    # here is cheap: the loop reconnects, and a lock that
                    # cannot finish the exchange would not have served us.
                    try:
                        async with asyncio.timeout(HANDSHAKE_TIMEOUT):
                            handshake_ok = await self._perform_handshake(ws)
                    except TimeoutError:
                        _LOGGER.warning(
                            f"[{self.serial_number}] Handshake timed out after "
                            f"{HANDSHAKE_TIMEOUT:.0f}s. The lock accepted the "
                            f"connection but never finished the exchange. "
                            f"Next attempt in 30s..."
                        )
                        self.ws_connected = False
                        self._active_ws = None
                        self.current_session_start = None
                        # The same 30 s the auth failure below uses, and for
                        # the same reason. A timeout is not a blip that clears
                        # itself: a lock that accepts the socket and stays
                        # silent keeps doing so until someone changes
                        # something on it. At 5 s this branch would open a TLS
                        # connection, hold it for the timeout and log a
                        # warning every fifteen seconds, indefinitely - which
                        # is the flooding the pause below exists to prevent.
                        await asyncio.sleep(30)
                        continue

                    if handshake_ok:
                        _LOGGER.info("WS Auth OK. Connection established.")
                        self.ws_connected = True
                        self._active_ws = ws
                        
                        # --- Track successful connection ---
                        self.connection_count += 1
                        self.current_session_start = time.time()
                        _LOGGER.info(
                            f"[{self.serial_number}] WS Connection #{self.connection_count} established."
                        )
                        # ------------------------------------
                        
                        _LOGGER.info("Sending initial status request after successful handshake...")
                        await self._send_status_nudge()
                        # -----------------------------
                        await self._listen(ws)

                        # _listen handles ConnectionClosed itself and returns
                        # normally, so the except branch below never sees it and
                        # its backoff does not apply. Without a pause here the
                        # while loop reconnects instantly - the same hammering
                        # pattern as a failed authentication, just triggered by
                        # a dropped session instead.
                        # The socket is always dead once _listen returns, so
                        # waiting inside the context manager costs nothing.
                        if self._monitor_running:
                            _LOGGER.info(
                                f"[{self.serial_number}] WS session ended after "
                                f"{self._format_session_duration()}. Reconnecting in 5s..."
                            )
                            await asyncio.sleep(5)
                    else:
                        # IMPORTANT: without a backoff the while loop would
                        # reconnect immediately -> endless loop that floods the
                        # lock with handshakes and spams the Home Assistant log.
                        _LOGGER.error(
                            f"[{self.serial_number}] WS Auth Failed. The serial "
                            f"number is part of the key, so check it as well as "
                            f"the password. Next attempt in 30s..."
                        )
                        self.ws_connected = False
                        self._active_ws = None
                        self.current_session_start = None
                        await asyncio.sleep(30)
            except Exception as e:
                _LOGGER.error(f"WS Error: {e}. Retrying in 5s...")
                self.ws_connected = False
                self._active_ws = None
                # --- Track disconnect ---
                self.current_session_start = None
                await asyncio.sleep(5)
