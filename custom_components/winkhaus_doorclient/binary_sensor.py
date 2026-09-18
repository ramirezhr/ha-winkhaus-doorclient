# in custom_components/winkhaus_doorclient/binary_sensor.py

import logging
from typing import Any
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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
    
    async_add_entities([WinkhausDoorSensor(coordinator, entry)])

class WinkhausDoorSensor(WinkhausEntity[WinkhausCoordinator], BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, coordinator: WinkhausCoordinator, entry: WinkhausConfigEntry) -> None:
        # Entity id says "door", the unique id has said "door_state" since
        # 1.2.0 - keep them apart rather than orphan existing entries.
        super().__init__(coordinator, entry, "binary_sensor", "door",
                         unique_key="door_state")

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return str(self.state_value("state")).lower() == "open"