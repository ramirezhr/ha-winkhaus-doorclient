# in custom_components/winkhaus_doorclient/api.py

import logging
import requests
import urllib3
import ssl
import asyncio
import websockets
import json
import struct
import os
import time
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from urllib3.util import ssl_
from typing import Optional, Dict, Any, List, Callable

# Cryptography
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.hmac import HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False):
        context = ssl_.create_urllib3_context(ciphers=None)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.options |= 0x4 
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=context
        )

class DoorClient:
    def __init__(self, serial_number: str, ip: str, password: str, port: int = 443, username: str = "admin"):
        self.serial_number = serial_number
        self.ip = ip
        self.port = port
        self.username = username
        self._password = password
        self._timeout = 15
        
        # HTTP Session Setup (Fallback & API)
        self.session = requests.Session()
        self.session.mount('https://', LegacySSLAdapter())
        
        # WebSocket Setup
        self.ws_port = 80
        self.ws_uri = f"ws://{self.ip}:{self.ws_port}/ws"
        self.ws_connected = False
        self._active_ws = None
        
        # Crypto & State
        self.shared_key = None
        self.device_challenge = None
        self.client_challenge = None
        self.client_counter = 0
        self.last_message_time = 0.0
        
        # Callbacks & Tasks
        self.on_state_change: Optional[Callable[[List[Dict[str, Any]]], None]] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._monitor_running = False
        
        # --- SIMPLE CONNECTION TRACKING ---
        self.connection_count = 0  # Total number of connections made
        self.current_session_start = None  # Timestamp of current session start
        # ------------------------------------

    # --- SIMPLE TRACKING METHODS ---
    def get_current_uptime(self) -> float:
        """Get current session uptime in seconds."""
        if self.current_session_start:
            return time.time() - self.current_session_start
        return 0.0
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
    def connect(self) -> bool:
        try:
            self.get_states()
            return True
        except Exception as err:
            _LOGGER.error(f"HTTP Connection failed: {err}")
            return False

    def _request(self, path: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        url = f"https://{self.ip}:{self.port}{path}"
        auth = (self.username, self._password)
        method = "POST" if data else "GET"
        
        try:
            response = self.session.request(
                method, url, json=data, auth=auth, verify=False, timeout=self._timeout
            )
            response.raise_for_status()
            if not response.content:
                return {}
            
            response_json = response.json()
            if "XC_ERR" in response_json:
                error_msg = response_json["XC_ERR"].get("text", "Unknown API error")
                raise Exception(f"Device Error: {error_msg}")

            return response_json.get("XC_SUC", {})

        except requests.exceptions.RequestException as e:
            raise Exception(f"Network error: {e}") from e

    def get_states(self) -> List[Dict[str, Any]]:
        raw_states = self._request("/api/v1/getStates")
        return self._format_states(raw_states)

    def _format_states(self, raw_states: dict) -> List[Dict[str, Any]]:
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

    def get_system_state(self) -> Dict[str, Any]:
        return self._request("/api/v1/getSystemState")

    def get_configuration(self) -> Dict[str, Any]:
        return self._request("/api/v1/getConfiguration")

    # --- ASYNC COMMAND & HYBRID LOGIC ---
    async def async_send_payload(self, endpoint: str, payload: Optional[Dict] = None) -> bool:
        
        if self.ws_connected and self._active_ws:
            try:
                self.client_counter += 1
                ws_data = f"{endpoint}\n{json.dumps(payload) if payload else '{}'}".encode('utf-8')
                iv = self._get_iv(self.client_challenge, self.client_counter)
                encrypted = AESCCM(self.shared_key, tag_length=16).encrypt(iv, ws_data, None)
                header = b'\x85\x00' + len(encrypted).to_bytes(2, 'big')
                
                await self._active_ws.send(header + self.client_counter.to_bytes(4, 'big') + encrypted)
                _LOGGER.debug(f"Command sent via WS to {endpoint}: {payload}")
                return True
            except Exception as e:
                _LOGGER.warning(f"WS send failed ({e}), falling back to HTTP.")

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._request, endpoint, payload)
            _LOGGER.debug(f"Command sent via HTTP to {endpoint}: {payload}")
            return True
        except Exception as e:
            _LOGGER.error(f"Send failed (WS & HTTP): {e}")
            return False

    async def async_execute_command(self, command: str, value: Optional[str] = None) -> bool:
        # Resolve the Home Assistant intent to a command the lock understands.
        # Reject anything unknown instead of sending an invalid payload.
        if command == "mode":
            if value not in VALID_MODES:
                _LOGGER.error(
                    f"[{self.serial_number}] Invalid mode '{value}'. "
                    f"Expected one of {VALID_MODES}. Command ignored."
                )
                return False
            device_command = value
        else:
            device_command = COMMAND_MAP.get(command)
            if device_command is None:
                _LOGGER.error(
                    f"[{self.serial_number}] Unknown command '{command}'. "
                    f"Expected one of {tuple(COMMAND_MAP)}. Command ignored."
                )
                return False
 
        success = await self.async_send_payload(
            "/api/v1/control", {"command": device_command}
        )

        if success and not self.ws_connected:
            _LOGGER.info(f"Command '{command}' sent via HTTP. Simulating push update...")
            await asyncio.sleep(3) 
            loop = asyncio.get_running_loop()
            fallback_data = await loop.run_in_executor(None, self.get_states)
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
    async def _watchdog_loop(self):
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
                    loop = asyncio.get_running_loop()
                    try:
                        fallback_data = await loop.run_in_executor(None, self.get_states)
                        if fallback_data and self.on_state_change:
                            self.on_state_change(fallback_data)
                        self.last_message_time = time.time()
                    except Exception as e:
                        _LOGGER.error(f"HTTP Fallback fetch failed: {e}")

    async def _listen(self, websocket):
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
                    counter = int.from_bytes(message[4:8], 'big')
                    iv = self._get_iv(self.device_challenge, counter)
                    decrypted = AESCCM(self.shared_key, tag_length=16).decrypt(iv, message[8:], None)
                    payload = decrypted.decode('utf-8')
                   
                    if payload.strip().startswith('{'):
                        data = json.loads(payload)
                        target = data.get("XC_SUC", data)
                        
                        if "state" in target or "mode" in target:
                            formatted_info = self._format_states(target)
                            _LOGGER.debug(f"WS Status Update: {formatted_info}")
                            if self.on_state_change:
                                self.on_state_change(formatted_info)
                        elif "XC_ERR" in data:
                            # XC_ERR means the lock REJECTED a command.
                            # Mirror the error extraction used in _request().
                            err = data.get("XC_ERR")
                            if isinstance(err, dict):
                                err_text = err.get("text", "Unknown error")
                            else:
                                err_text = str(err)
                            _LOGGER.warning(
                                f"[{self.serial_number}] Lock rejected command: "
                                f"{err_text} (raw: {data})"
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
            self.ws_connected = False
            self._active_ws = None
            self.current_session_start = None
            
            if self._watchdog_task:
                self._watchdog_task.cancel()

    async def stop(self):
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
            
        try:
            self.session.close()
        except Exception:
            pass
        # ---------------------------------------------------
            
        _LOGGER.debug("DoorClient background tasks stopped successfully.")
        
    async def connect_and_monitor(self):
        self._monitor_running = True
        while self._monitor_running:
            ssl_ctx = ssl.create_default_context() if self.ws_uri.startswith("wss") else None
            if ssl_ctx: ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE

            try:
                _LOGGER.info(f"Connecting WS to {self.ws_uri}...")
                async with websockets.connect(self.ws_uri, ssl=ssl_ctx, ping_interval=20, ping_timeout=10, close_timeout=5) as ws:
                    # Handshake 
                    msg = await ws.recv()
                    srv = msg[2:] if len(msg) == 66 else msg
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