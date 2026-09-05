# in custom_components/winkhaus_doorclient/sensor.py

import logging
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_UPDATE_MODE, MODE_HYBRID, build_entity_id

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    system_coordinator = data["system_coordinator"]
    coordinator = data["coordinator"]
    device_info = data["device_info"] 

    async_add_entities([
        WinkhausLockCountSensor(system_coordinator, entry, device_info),
        WinkhausUnlockCountSensor(system_coordinator, entry, device_info),
        WinkhausErrorCountSensor(system_coordinator, entry, device_info),
        WinkhausConnectionModeSensor(entry, device_info),
        WinkhausErrorStateSensor(coordinator, entry, device_info),
    ])

class WinkhausSystemSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, device_info: dict, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.data['serial_number']}_{key}"
        self.entity_id = build_entity_id("sensor", entry.data['serial_number'], key)
        # The payload key doubles as the translation key, so a sensor is
        # named in exactly one place: the translation files.
        self._attr_translation_key = key
        self._attr_device_info = device_info

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        
        return self.coordinator.data.get(self._key)

class WinkhausLockCountSensor(WinkhausSystemSensor):
    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "lock_cnt")
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:lock-check"

class WinkhausUnlockCountSensor(WinkhausSystemSensor):
    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "unlock_cnt")
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:lock-open-variant"

class WinkhausErrorCountSensor(WinkhausSystemSensor):
    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "error_cnt")
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:alert-circle-outline"

class WinkhausConnectionModeSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, device_info: dict) -> None:
        self._entry = entry
        self._attr_device_info = device_info
        self._attr_unique_id = f"{entry.data['serial_number']}_connection_mode"
        self.entity_id = build_entity_id("sensor", entry.data['serial_number'], "connection_mode")
        self._attr_translation_key = "connection_mode"

    @property
    def native_value(self):
        mode = self._entry.options.get(CONF_UPDATE_MODE, MODE_HYBRID)
        return "Hybrid" if mode == MODE_HYBRID else "Polling"

    @property
    def icon(self):
        mode = self._entry.options.get(CONF_UPDATE_MODE, MODE_HYBRID)
        return "mdi:lan-connect" if mode == MODE_HYBRID else "mdi:cached"

class WinkhausErrorStateSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, device_info: dict) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data['serial_number']}_error_state"
        self.entity_id = build_entity_id("sensor", entry.data['serial_number'], "error_state")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:alert-circle-outline"
        self._attr_device_info = device_info

        # Supplies both the entity name and the state labels from
        # strings.json. Setting _attr_name as well would override the
        # translated name and leave it English in every language.
        self._attr_translation_key = "error_state"

    def _active_errors(self) -> list[str]:
        """Faults currently reported by the lock, normalised to a list.

        The lock omits the key entirely when nothing is wrong, and reports a
        list once something is. A single value is wrapped so callers never
        have to distinguish the two shapes.
        """
        if not self.coordinator.data:
            return []

        # api.py delivers a list of dicts: [{'name': 'locked', 'value': False}, ...]
        raw = next(
            (item["value"] for item in self.coordinator.data if item["name"] == "error"),
            None,
        )

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
        return errors[0]

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        errors = self._active_errors()
        return {
            "all_errors": errors,
            "error_count": len(errors),
        }