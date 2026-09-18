# in custom_components/winkhaus_doorclient/select.py

import logging
from typing import Any
import asyncio
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import WinkhausConfigEntry, WinkhausCoordinator
from .entity import WinkhausEntity

_LOGGER = logging.getLogger(__name__)

# All entities read from the same coordinator and every command goes to the
# same lock, so there is nothing to serialise.
PARALLEL_UPDATES = 0
MODES = ["day", "night"]

async def async_setup_entry(
    hass: HomeAssistant,
    entry: WinkhausConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    
    async_add_entities([WinkhausModeSelect(coordinator, entry)])

class WinkhausModeSelect(WinkhausEntity[WinkhausCoordinator], SelectEntity):
    _attr_options = MODES

    def __init__(self, coordinator: WinkhausCoordinator, entry: WinkhausConfigEntry) -> None:
        super().__init__(coordinator, entry, "select", "mode")

    @property
    def current_option(self) -> str | None:
        if not self.coordinator.data:
            return None
        mode: str | None = self.state_value("mode")
        return mode
        
    async def async_select_option(self, option: str) -> None:
        if not await self.client.async_execute_command("mode", option):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mode_failed",
                translation_placeholders={"mode": option},
            )