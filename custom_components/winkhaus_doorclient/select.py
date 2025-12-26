# in custom_components/winkhaus_door/select.py

import logging
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .api import DoorClient

_LOGGER = logging.getLogger(__name__)
MODES = ["day", "night"]

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    coordinator = data["coordinator"]
    async_add_entities([WinkhausModeSelect(coordinator, client, entry)])

class WinkhausModeSelect(CoordinatorEntity, SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, client: DoorClient, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._client = client
        self._attr_unique_id = f"{entry.data['serial_number']}_mode"
        self._attr_name = "Mode"
        self._attr_options = MODES
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.data['serial_number'])},
        }

    @property
    def current_option(self) -> str | None:
        if not self.coordinator.data:
            return None
        return next((item['value'] for item in self.coordinator.data if item['name'] == 'mode'), None)
        
    @property
    def icon(self) -> str:
        if self.current_option == "night":
            return "mdi:weather-night"  # Mond-Icon
        return "mdi:weather-sunny"      # Sonnen-Icon

    async def async_select_option(self, option: str) -> None:
        await self.hass.async_add_executor_job(self._client.execute_command, "mode", option)
        await self.coordinator.async_request_refresh()