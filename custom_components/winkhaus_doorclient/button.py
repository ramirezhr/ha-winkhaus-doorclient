# in custom_components/winkhaus_doorclient/button.py

import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import EntityCategory

from .const import DOMAIN, build_entity_id
from .coordinator import WinkhausConfigEntry
from .api import DoorClient

_LOGGER = logging.getLogger(__name__)

# All entities read from the same coordinator and every command goes to the
# same lock, so there is nothing to serialise.
PARALLEL_UPDATES = 0

async def async_setup_entry(
    hass: HomeAssistant,
    entry: WinkhausConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    client = entry.runtime_data.client
    
    async_add_entities([WinkhausUnblockButton(client, entry)])

class WinkhausUnblockButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, client: DoorClient, entry: WinkhausConfigEntry) -> None:
        # Not coordinator-driven, so this one wires itself up. The unique id
        # has said "unblock" since 2.2.0 while the entity id says
        # "clear_errors" - keep them apart rather than orphan entries.
        self._client = client
        self._attr_unique_id = f"{entry.data['serial_number']}_unblock"
        self.entity_id = build_entity_id("button", entry.data['serial_number'], "clear_errors")
        self._attr_translation_key = "clear_errors"
        self._attr_device_info = entry.runtime_data.device_info
        
        
        self._attr_entity_category = EntityCategory.CONFIG 

    async def async_press(self) -> None:
        # async_unblock reports a refusal by returning False rather than
        # raising, so both outcomes have to be handled.
        try:
            sent = await self._client.async_unblock()
        except Exception as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unblock_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        if not sent:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unblock_unreachable",
            )