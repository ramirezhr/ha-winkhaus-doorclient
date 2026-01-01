# in custom_components/winkhaus_doorclient/api.py

import logging
import requests
import ssl
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from typing import Optional, Dict, Any, List
import time

_LOGGER = logging.getLogger(__name__)

WINKHAUS_STATUS_MAP = {
    "state": ["open", "closed"],
    "mode": ["day", "night"]
}

LEGACY_CIPHERS = ('DEFAULT@SECLEVEL=1')

class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False):
        ssl_context = create_urllib3_context(ssl_version=ssl.PROTOCOL_TLSv1_2)
        ssl_context.set_ciphers(LEGACY_CIPHERS)
        ssl_context.check_hostname = False
        self.poolmanager = requests.packages.urllib3.PoolManager(
            num_pools=connections, maxsize=maxsize, block=block, ssl_context=ssl_context)

class DoorClient:
    def __init__(self, serial_number: str, ip: str, password: str, port: int = 443, username: str = "admin"):
        self.serial_number, self.ip, self.port, self.username, self.password = serial_number, ip, port, username, password
        self.state = "disconnected"
        self.session = requests.Session()
        self.session.mount('https://', LegacySSLAdapter())

    def connect(self) -> bool:
        try:
            self.get_states()
            self.state = "connected"
            return True
        except Exception:
            self.state = "error"
            return False

    def _request(self, path: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        url = f"https://{self.ip}:{self.port}{path}"
        auth = (self.username, self.password)
        method = "POST" if data else "GET"
        try:
            response = self.session.request(method, url, json=data, auth=auth, verify=False, timeout=15)
            response.raise_for_status()
            response_json = response.json() if response.content else {}
            if "XC_ERR" in response_json:
                raise Exception(response_json["XC_ERR"].get("text", "Unbekannter Fehler vom Geraet"))
            return response_json.get("XC_SUC", {})
        except requests.exceptions.RequestException as e:
            raise e

    def get_states(self) -> List[Dict[str, Any]]:
        raw_states = self._request("/api/v1/getStates")
        interpreted_states = []
        for key, raw_value in raw_states.items():
            logical_value = raw_value
            if key in WINKHAUS_STATUS_MAP and isinstance(raw_value, int) and raw_value < len(WINKHAUS_STATUS_MAP[key]):
                logical_value = WINKHAUS_STATUS_MAP[key][raw_value]
            interpreted_states.append({"name": key, "value": logical_value})
        return interpreted_states

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