# in custom_components/winkhaus_doorclient/button.py

import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import EntityCategory

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
    device_info = data["device_info"]
    
    async_add_entities([WinkhausUnblockButton(client, entry, device_info)])

class WinkhausUnblockButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, client: DoorClient, entry: ConfigEntry, device_info: dict) -> None:
        self._client = client
        self._attr_unique_id = f"{entry.data['serial_number']}_unblock"
        self._attr_translation_key = "clear_errors"
        self._attr_device_info = device_info
        
        self._attr_icon = "mdi:shield-refresh"
        
        self._attr_entity_category = EntityCategory.CONFIG 

    async def async_press(self) -> None:
        try:
            await self._client.async_unblock()
        except Exception as e:
            _LOGGER.error(f"Unblock failed: {e}")
            raise