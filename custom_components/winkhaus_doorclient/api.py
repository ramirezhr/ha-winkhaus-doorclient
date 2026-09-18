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
        
        # Callbacks & Tasks
        self.on_state_change: Callable[[list[dict[str, Any]]], None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._monitor_running = False
        
        # --- SIMPLE CONNECTION TRACKING ---
        self.connection_count = 0  # Total number of connections made
        self.current_session_start: float | None = None  # Timestamp of current session start
        self.last_session_seconds = 0.0  # Duration of the session that just ended
        # ------------------------------------

        # --- FRAGMENT REASSEMBLY ---
        self._rx_buffer = bytearray()  # Decrypted plaintext of pending fragments
        self._rx_type: int | None = None  # Packet type nibble of the pending message
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
            error_msg = response_json["XC_ERR"].get("text", "Unknown API error")
            raise Exception(f"Device Error: {error_msg}")

        result: dict[str, Any] = response_json.get("XC_SUC", {})
        return result

    async def get_states(self) -> list[dict[str, Any]]:
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

    async def get_system_state(self) -> dict[str, Any]:
        return await self._request("/api/v1/getSystemState")

    async def get_configuration(self) -> dict[str, Any]:
        return await self._request("/api/v1/getConfiguration")

    # --- ASYNC COMMAND & HYBRID LOGIC ---
    async def async_send_payload(self, endpoint: str, payload: dict[str, Any] | None = None) -> bool:
        
        if self.ws_connected and self._active_ws:
            if self.shared_key is None or self.client_challenge is None:
                raise RuntimeError("Send attempted before the handshake completed")
            try:
                self.client_counter += 1
                ws_data = f"{endpoint}\n{json.dumps(payload) if payload else '{}'}".encode('utf-8')
                iv = self._get_iv(self.client_challenge, self.client_counter)
                encrypted = AESCCM(self.shared_key, tag_length=16).encrypt(iv, ws_data, None)
                header = b'\x85\x00' + len(encrypted).to_bytes(2, 'big')
                
                await self._active_ws.send(header + self.client_counter.to_bytes(4, 'big') + encrypted)

                # Remember what went out last. A rejection arrives
                # asynchronously and carries no reference to its request, so
                # this is the only way to name the offending command.
                self._last_request = (endpoint, payload, time.time())

                _LOGGER.debug(f"Command sent via WS to {endpoint}: {payload}")
                return True
            except Exception as e:
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
            fallback_data = await self.get_states()
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
        _LOGGER.info("Watchdog started (75s trigger interval).")
        while True:
            await asyncio.sleep(5)
            time_since_last = time.time() - self.last_message_time
            
            if time_since_last > 75 and self.ws_connected and self._active_ws:
                _LOGGER.debug(f"Watchdog: {int(time_since_last)}s no message. Pinging...")
                try:
                    await self.async_send_payload("/api/v1/getStates", {})
                    await asyncio.sleep(5)
                except Exception as e:
                    _LOGGER.debug(f"Watchdog ping failed: {e}")

                if time.time() - self.last_message_time > 85:
                    _LOGGER.warning("WS unresponsive. Triggering HTTP Fallback fetch.")
                    try:
                        fallback_data = await self.get_states()
                        if fallback_data and self.on_state_change:
                            self.on_state_change(fallback_data)
                        self.last_message_time = time.time()
                    except Exception as e:
                        _LOGGER.error(f"HTTP Fallback fetch failed: {e}")

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

                    # The device splits large payloads into chunks of up to
                    # 1024 bytes and only sets the 0x80 bit on the last one.
                    # Every chunk is encrypted separately with its own counter,
                    # so decrypt first and concatenate the plaintext.
                    if self._rx_type is not None and packet_type != self._rx_type:
                        _LOGGER.debug(
                            f"Packet type changed from {self._rx_type} to {packet_type}, "
                            f"dropping {len(self._rx_buffer)} buffered bytes."
                        )
                        self._rx_buffer.clear()

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

                    self._rx_buffer.extend(decrypted)
                    self._rx_type = packet_type

                    if not is_final:
                        # A lost final fragment would otherwise grow this
                        # buffer without bound on a long-lived connection.
                        if len(self._rx_buffer) > 65536:
                            _LOGGER.warning(
                                f"[{self.serial_number}] Reassembly buffer exceeded 64 KB "
                                f"without a final fragment. Discarding."
                            )
                            self._rx_buffer.clear()
                            self._rx_type = None
                        continue

                    # Decode only once the message is complete: a fragment can
                    # end in the middle of a multi-byte UTF-8 character.
                    payload = bytes(self._rx_buffer).decode('utf-8')
                    self._rx_buffer.clear()
                    self._rx_type = None
                   
                    if payload.strip().startswith('{'):
                        data = json.loads(payload)
                        target = data.get("XC_SUC", data)
                        
                        if "state" in target or "mode" in target:
                            formatted_info = self._format_states(target)
                            _LOGGER.debug(f"WS Status Update: {formatted_info}")
                            if self.on_state_change:
                                self.on_state_change(formatted_info)
                        elif "XC_ERR" in data:
                            # XC_ERR means the lock REJECTED a request.
                            # Mirror the error extraction used in _request().
                            err = data.get("XC_ERR")
                            if isinstance(err, dict):
                                err_text = err.get("text", "Unknown error")
                            else:
                                err_text = str(err)
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
            self._rx_buffer.clear()
            self._rx_type = None

            # The device restarts its counter on every handshake, so a stale
            # value here would reject every message of the next session.
            self._device_counter = None
            
            if self._watchdog_task:
                self._watchdog_task.cancel()

    async def stop(self) -> None:
        self._monitor_running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
            
        if self.ws_connected and self._active_ws:
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
        
    async def connect_and_monitor(self) -> None:
        self._monitor_running = True
        while self._monitor_running:
            ssl_ctx = ssl.create_default_context() if self.ws_uri.startswith("wss") else None
            if ssl_ctx: ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE

            try:
                _LOGGER.info(f"Connecting WS to {self.ws_uri}...")
                async with websockets.connect(self.ws_uri, ssl=ssl_ctx, ping_interval=20, ping_timeout=10, close_timeout=5) as ws:
                    # Handshake 
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
                    
                    if check == self._create_hmac_sha1(pwd_key, self.client_challenge):
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
                        await self.async_send_payload("/api/v1/getStates", {})
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
                            f"[{self.serial_number}] WS Auth Failed. "
                            f"Check the password. Next attempt in 30s..."
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