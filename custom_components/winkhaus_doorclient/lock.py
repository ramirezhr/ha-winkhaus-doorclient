# in custom_components/winkhaus_doorclient/lock.py

import logging
from typing import Any
import asyncio
from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_platform
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import WinkhausConfigEntry, WinkhausCoordinator
from .entity import WinkhausEntity

_LOGGER = logging.getLogger(__name__)

# All entities read from the same coordinator and every command goes to the
# same lock, so there is nothing to serialise.
PARALLEL_UPDATES = 0

async def async_setup_entry(
    hass: HomeAssistant,
    entry: WinkhausConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    
    async_add_entities([WinkhausLock(coordinator, entry)])
    
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

class WinkhausLock(WinkhausEntity[WinkhausCoordinator], LockEntity):
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: WinkhausCoordinator, entry: WinkhausConfigEntry) -> None:
        # The lock predates the suffixed scheme: its unique id is the bare
        # serial number, and changing it would orphan every existing entry.
        super().__init__(coordinator, entry, "lock", "lock", unique_key="")

    @property
    def is_locked(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return str(self.state_value("locked")).lower() == "true"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        
        attributes: dict[str, Any] = {}
        
        # Add standard state attributes
        for item in self.coordinator.data:
            key = item["name"]
            value = item["value"]
            
            if key == "time":
                # _device_time_to_iso validates the value itself and returns
                # None for anything it cannot make sense of.
                timestamp = self._device_time_to_iso(value)
                if timestamp:
                    attributes["last_update_from_device"] = timestamp
            else:
                attributes[key] = value
        
        # --- ADD CONNECTION TRACKING ATTRIBUTES ---
        # WebSocket connection status
        attributes["websocket_connected"] = self.client.ws_connected
        
        # Connection count
        attributes["connection_count"] = self.client.connection_count
        
        # Current session uptime
        uptime_seconds = self.client.get_current_uptime()
        if uptime_seconds > 0:
            attributes["current_uptime"] = self._format_uptime(uptime_seconds)
            attributes["current_uptime_seconds"] = round(uptime_seconds, 1)
        else:
            attributes["current_uptime"] = "Not connected"
            attributes["current_uptime_seconds"] = 0.0
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
        except (OverflowError, OSError, TypeError, ValueError):
            # Lock reported a nonsensical timestamp (e.g. uninitialised clock)
            return None

    async def _execute(self, command: str, value: str | None = None) -> None:
        """Send a command and report a failure to the caller.

        async_execute_command returns False when neither WebSocket nor HTTP
        got through. Ignoring that left the user pressing a button with no
        feedback at all, while the entity kept showing the old state.
        """
        if not await self.client.async_execute_command(command, value):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"command": value or command},
            )

    async def async_get_system_state(self) -> None:
        try:
            state = await self.client.get_system_state()
        except Exception as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="system_state_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        _LOGGER.warning(f"SYSTEM STATE DUMP:\n{state}")

    async def async_set_day_mode(self) -> None:
        await self._execute("mode", "day")

    async def async_set_night_mode(self) -> None:
        await self._execute("mode", "night")

    async def async_lock(self, **kwargs: Any) -> None:
        await self._execute("night")

    async def async_unlock(self, **kwargs: Any) -> None:
        await self._execute("day")

    async def async_open(self, **kwargs: Any) -> None:
        await self._execute("open")