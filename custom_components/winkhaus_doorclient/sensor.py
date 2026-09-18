# in custom_components/winkhaus_doorclient/sensor.py

import logging
from typing import Any
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_UPDATE_MODE, MODE_HYBRID, build_entity_id
from .coordinator import (
    WinkhausConfigEntry,
    WinkhausCoordinator,
    WinkhausSystemCoordinator,
)
from .entity import WinkhausEntity

_LOGGER = logging.getLogger(__name__)

# Faults the lock is known to report. Declaring them as an enum lets Home
# Assistant validate the state and offer it in automation pickers.
KNOWN_FAULTS = ("blocked", "overcurrent", "batterylow")

# A value outside that set is reported as "unknown_fault" rather than raw:
# an enum sensor rejects an undeclared state and would go unavailable, so a
# firmware adding a fault code would break the sensor instead of showing it.
UNKNOWN_FAULT = "unknown_fault"

ERROR_STATES = ("none", *KNOWN_FAULTS, UNKNOWN_FAULT)

# All entities read from the same coordinator and every command goes to the
# same lock, so there is nothing to serialise.
PARALLEL_UPDATES = 0

async def async_setup_entry(
    hass: HomeAssistant,
    entry: WinkhausConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    system_coordinator = entry.runtime_data.system_coordinator
    coordinator = entry.runtime_data.coordinator

    async_add_entities([
        WinkhausLockCountSensor(system_coordinator, entry),
        WinkhausUnlockCountSensor(system_coordinator, entry),
        WinkhausErrorCountSensor(system_coordinator, entry),
        WinkhausConnectionModeSensor(entry),
        WinkhausErrorStateSensor(coordinator, entry),
    ])

class WinkhausSystemSensor(WinkhausEntity[WinkhausSystemCoordinator], SensorEntity):
    def __init__(
        self, coordinator: WinkhausSystemCoordinator, entry: WinkhausConfigEntry, key: str
    ) -> None:
        # The payload key doubles as entity id suffix and translation key,
        # so a sensor is named in exactly one place: the translation files.
        super().__init__(coordinator, entry, "sensor", key)
        self._key = key

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        
        return self.coordinator.data.get(self._key)

class WinkhausLockCountSensor(WinkhausSystemSensor):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: WinkhausSystemCoordinator, entry: WinkhausConfigEntry) -> None:
        super().__init__(coordinator, entry, "lock_cnt")

class WinkhausUnlockCountSensor(WinkhausSystemSensor):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: WinkhausSystemCoordinator, entry: WinkhausConfigEntry) -> None:
        super().__init__(coordinator, entry, "unlock_cnt")

class WinkhausErrorCountSensor(WinkhausSystemSensor):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: WinkhausSystemCoordinator, entry: WinkhausConfigEntry) -> None:
        super().__init__(coordinator, entry, "error_cnt")

class WinkhausConnectionModeSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: WinkhausConfigEntry) -> None:
        # Not coordinator-driven: the mode comes from the entry options, not
        # from the lock, so this one keeps its own wiring.
        self._entry = entry
        self._attr_device_info = entry.runtime_data.device_info
        self._attr_unique_id = f"{entry.data['serial_number']}_connection_mode"
        self.entity_id = build_entity_id("sensor", entry.data['serial_number'], "connection_mode")
        self._attr_translation_key = "connection_mode"

    @property
    def native_value(self) -> Any:
        mode = self._entry.options.get(CONF_UPDATE_MODE, MODE_HYBRID)
        return "Hybrid" if mode == MODE_HYBRID else "Polling"

class WinkhausErrorStateSensor(WinkhausEntity[WinkhausCoordinator], SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(ERROR_STATES)

    def __init__(self, coordinator: WinkhausCoordinator, entry: WinkhausConfigEntry) -> None:
        # The translation key supplies both the entity name and the state
        # labels. Setting _attr_name as well would override the translated
        # name and leave it English in every language.
        super().__init__(coordinator, entry, "sensor", "error_state")

    def _active_errors(self) -> list[str]:
        """Faults currently reported by the lock, normalised to a list.

        The lock omits the key entirely when nothing is wrong, and reports a
        list once something is. A single value is wrapped so callers never
        have to distinguish the two shapes.
        """
        if not self.coordinator.data:
            return []

        raw = self.state_value("error")

        if not raw:
            return []
        if isinstance(raw, list):
            return [str(entry) for entry in raw]
        return [str(raw)]

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None

        errors = self._active_errors()
        if not errors:
            return "none"

        # Only the first fault becomes the state, because every state needs a
        # matching entry in strings.json to be translated. Joining several
        # into "blocked, overcurrent" produced a value no translation covers,
        # so the dashboard fell back to the raw English string. The full list
        # stays available as an attribute.
        primary = errors[0]

        if primary not in KNOWN_FAULTS:
            _LOGGER.warning(
                "Lock reported an unknown fault: %s. Please open an issue so "
                "it can be added.",
                primary,
            )
            return UNKNOWN_FAULT

        return primary

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        errors = self._active_errors()
        return {
            "all_errors": errors,
            "error_count": len(errors),
        }



