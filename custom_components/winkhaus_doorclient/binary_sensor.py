# in custom_components/winkhaus_door/binary_sensor.py

import logging
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    async_add_entities([WinkhausDoorSensor(coordinator, client, entry)])

class WinkhausDoorSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, client: DoorClient, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._client = client
        self._attr_unique_id = f"{entry.data['serial_number']}_door_state"
        self._attr_name = "Door"
        self._attr_device_class = BinarySensorDeviceClass.DOOR
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.data['serial_number'])},
        }

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        state_value = next((item['value'] for item in self.coordinator.data if item['name'] == 'state'), None)
        return str(state_value).lower() == 'open'