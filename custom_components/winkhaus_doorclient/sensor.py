# in custom_components/winkhaus_doorclient/sensor.py

import logging
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    system_coordinator = data["system_coordinator"]
    device_info = data["device_info"] 

    async_add_entities([
        WinkhausLockCountSensor(system_coordinator, entry, device_info),
        WinkhausUnlockCountSensor(system_coordinator, entry, device_info),
        WinkhausErrorCountSensor(system_coordinator, entry, device_info),
    ])

class WinkhausSystemSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, device_info: dict, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.data['serial_number']}_{key}"
        self._attr_name = name
        self._attr_device_info = device_info

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        
        return self.coordinator.data.get(self._key)

class WinkhausLockCountSensor(WinkhausSystemSensor):
    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "lock_cnt", "Lock Count")
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:lock-check"

class WinkhausUnlockCountSensor(WinkhausSystemSensor):
    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "unlock_cnt", "Unlock Count")
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:lock-open-variant"

class WinkhausErrorCountSensor(WinkhausSystemSensor):
    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "error_cnt", "Error Count")
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:alert-circle-outline"