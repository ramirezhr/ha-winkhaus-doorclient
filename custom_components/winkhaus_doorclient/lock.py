# in custom_components/winkhaus_door/lock.py

import logging
from datetime import datetime
from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
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
    async_add_entities([WinkhausLock(coordinator, client, entry)])

class WinkhausLock(CoordinatorEntity, LockEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, client: DoorClient, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._client = client
        self._attr_unique_id = entry.data["serial_number"]
        self._attr_name = "Lock"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": f"Winkhaus Door ({entry.data['serial_number']})",
            "manufacturer": "Winkhaus",
        }
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
        for item in self.coordinator.data:
            key = item["name"]
            value = item["value"]
            
            if key == "time" and isinstance(value, (int, float)):
                attributes["last_update_from_device"] = datetime.fromtimestamp(value).isoformat()
            elif key not in ["time"]:
                attributes[key] = value

        return attributes

    async def async_lock(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(self._client.execute_command, "night")
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(self._client.execute_command, "day")
        await self.coordinator.async_request_refresh()

    async def async_open(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(self._client.execute_command, "open")
        await self.coordinator.async_request_refresh()