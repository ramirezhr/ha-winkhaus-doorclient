# in custom_components/winkhaus_doorclient/lock.py

import logging
import asyncio
from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import entity_platform
from homeassistant.util import dt as dt_util

from .const import DOMAIN, build_entity_id
from .api import DoorClient

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    coordinator = data["coordinator"]
    device_info = data["device_info"] 
    
    async_add_entities([WinkhausLock(coordinator, client, entry, device_info)])
    
    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        "set_day_mode", {}, "async_set_day_mode"
    )
    platform.async_register_entity_service(
        "set_night_mode", {}, "async_set_night_mode"
    )
    platform.async_register_entity_service(
        "get_system_state", {}, "async_get_system_state"
    )

class WinkhausLock(CoordinatorEntity, LockEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, client: DoorClient, entry: ConfigEntry, device_info: dict) -> None:
        super().__init__(coordinator)
        self._client = client
        self._attr_unique_id = entry.data["serial_number"]
        self.entity_id = build_entity_id("lock", entry.data["serial_number"], "lock")
        self._attr_name = "Lock"
        self._attr_device_info = device_info
        self._attr_supported_features = LockEntityFeature.OPEN

    @property
    def is_locked(self) -> bool | None:
        if not self.coordinator.data:
            return None
        locked_state = next((item['value'] for item in self.coordinator.data if item['name'] == 'locked'), None)
        return str(locked_state).lower() == 'true'

    @property
    def extra_state_attributes(self) -> dict | None:
        if not self.coordinator.data:
            return None
        
        attributes = {}
        
        # Add standard state attributes
        for item in self.coordinator.data:
            key = item["name"]
            value = item["value"]
            
            if key == "time" and isinstance(value, (int, float)):
                attributes["last_update_from_device"] = self._device_time_to_iso(value)
            elif key not in ["time"]:
                attributes[key] = value
        
        # --- ADD CONNECTION TRACKING ATTRIBUTES ---
        # WebSocket connection status
        attributes["websocket_connected"] = self._client.ws_connected
        
        # Connection count
        attributes["connection_count"] = self._client.connection_count
        
        # Current session uptime
        uptime_seconds = self._client.get_current_uptime()
        if uptime_seconds > 0:
            attributes["current_uptime"] = self._format_uptime(uptime_seconds)
            attributes["current_uptime_seconds"] = round(uptime_seconds, 1)
        else:
            attributes["current_uptime"] = "Not connected"
            attributes["current_uptime_seconds"] = 0
        # -------------------------------------------

        return attributes

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        """Format an uptime as HH:MM:SS with hours accumulating past 24.

        str(timedelta()) switches its structure once a session passes the
        24 hour mark ("1 day, 1:01:01" instead of "1:01:01"), which breaks
        templates that parse the string. Hours simply keep counting up
        here, so the value always consists of three numeric fields.
        """
        total = int(seconds)
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    
    @staticmethod
    def _device_time_to_iso(value: int | float) -> str | None:
        """Convert the lock's timestamp into a timezone-aware ISO string.

        The lock reports a standard UTC Unix timestamp. Tagging it explicitly
        as UTC makes the result independent of the host's system time zone,
        which differs between HA OS and container installs. Home Assistant
        then renders it in the user's configured zone.

        The previous code produced a naive string, so the frontend fell back
        to interpreting it as local time and displayed the value shifted by
        the UTC offset.
        """
        try:
            return dt_util.utc_from_timestamp(float(value)).isoformat()
        except (OverflowError, OSError, ValueError):
            # Lock reported a nonsensical timestamp (e.g. uninitialised clock)
            return None

    async def async_get_system_state(self):
        try:
            state = await self.hass.async_add_executor_job(self._client.get_system_state)
            _LOGGER.warning(f"SYSTEM STATE DUMP:\n{state}")
        except Exception as err:
            _LOGGER.error(f"Error fetching system state: {err}")

    async def async_set_day_mode(self):
        await self._client.async_execute_command("mode", "day")

    async def async_set_night_mode(self):
        await self._client.async_execute_command("mode", "night")

    async def async_lock(self, **kwargs) -> None:
        await self._client.async_execute_command("night")

    async def async_unlock(self, **kwargs) -> None:
        await self._client.async_execute_command("day")

    async def async_open(self, **kwargs) -> None:
        await self._client.async_execute_command("open")