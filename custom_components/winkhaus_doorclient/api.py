# in custom_components/winkhaus_doorclient/api.py

import logging
import requests
import ssl
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from urllib3.util import ssl_
from typing import Optional, Dict, Any, List

_LOGGER = logging.getLogger(__name__)

WINKHAUS_STATUS_MAP = {
    "state": ["open", "closed"],
    "mode": ["day", "night"]
}

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
        self.password = password     
        self.session = requests.Session()
        self.session.mount('https://', LegacySSLAdapter())
        self._timeout = 15

    def connect(self) -> bool:
        try:
            self.get_states()
            return True
        except Exception as err:
            _LOGGER.error(f"Verbindung fehlgeschlagen: {err}")
            return False

    def _request(self, path: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        url = f"https://{self.ip}:{self.port}{path}"
        auth = (self.username, self.password)
        method = "POST" if data else "GET"
        
        try:
            response = self.session.request(
                method, 
                url, 
                json=data, 
                auth=auth, 
                verify=False, 
                timeout=self._timeout
            )
            
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 401:
                    raise Exception("Authentifizierung fehlgeschlagen (Falsches Passwort?)") from e
                raise Exception(f"HTTP Fehler: {e.response.status_code}") from e

            if not response.content:
                return {}

            try:
                response_json = response.json()
            except ValueError:
                _LOGGER.error(f"The API did not return valid JSON: {response.text[:100]}...")
                raise Exception("Invalid data format received from device")

            if "XC_ERR" in response_json:
                error_info = response_json["XC_ERR"]
                error_msg = error_info.get("text", "Unbekannter API-Fehler")
                raise Exception(f"Geräte-Fehler: {error_msg}")

            return response_json.get("XC_SUC", {})

        except requests.exceptions.RequestException as e:
            raise Exception(f"Netzwerkfehler: {e}") from e

    def get_states(self) -> List[Dict[str, Any]]:
        raw_states = self._request("/api/v1/getStates")
        
        if not isinstance(raw_states, dict):
            _LOGGER.warning(f"getStates lieferte unerwarteten Datentyp: {type(raw_states)}")
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

    def execute_command(self, command: str, value: Optional[str] = None) -> bool:
        payload = {}     
        if command == "mode" and value in ["day", "night"]:
            payload["command"] = value
        elif command == "open":
             payload["command"] = "unlock"
        elif command == "lock":
            payload["command"] = "night"
        elif command == "unlock":
            payload["command"] = "day"
        else:
            payload["command"] = command
        
        self._request("/api/v1/control", data=payload)
        return True