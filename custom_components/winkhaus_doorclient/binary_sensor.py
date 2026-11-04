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

# The door contact is optional on an EAV4+: the lock reports "open" or
# "closed" when a magnet clip is fitted and declared in the doorControl app,
# and "nocontact" otherwise. A blueMotion+ has no such option and always
# reports the position. So the value describes the installation, and fitting
# the clip starts the reporting without anything changing here. Anything
# outside the two known values means the position is not being reported, and
# a door sensor has to say so rather than pick the safe-sounding half of a
# guess.
DOOR_STATES = {"open": True, "closed": False}

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
        raw = self.state_value("state")
        if raw is None:
            return None

        value = str(raw).lower()
        if value not in DOOR_STATES:
            _LOGGER.debug(
                "Door state %r is neither open nor closed, reporting unknown.", raw
            )
            return None
        return DOOR_STATES[value]
