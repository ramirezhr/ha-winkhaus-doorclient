# in custom_components/winkhaus_doorclient/__init__.py

from datetime import timedelta
import logging
import json

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DoorClient
from .const import DOMAIN

PLATFORMS = ["lock", "select", "binary_sensor"]
_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    client = await hass.async_add_executor_job(
        lambda: DoorClient(
            serial_number=entry.data["serial_number"],
            ip=entry.data[CONF_IP_ADDRESS],
            password=entry.data[CONF_PASSWORD],
            username=entry.data[CONF_USERNAME]
        )
    )

    async def async_update_data():
        _LOGGER.debug(">>> [COORDINATOR] Starte zentrale Statusabfrage...")
        try:
            return await hass.async_add_executor_job(client.get_states)
        except Exception as err:
            _LOGGER.error(f"!!! [COORDINATOR] Fehler bei der zentralen Abfrage: {err}")
            raise UpdateFailed(f"Fehler bei der Kommunikation mit API: {err}")

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=60),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    # Dienste registrieren
    await async_setup_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    
    if not hass.data[DOMAIN]:
        async_remove_services(hass)
        
    return unload_ok

async def async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "set_day_mode"):
        return

    _LOGGER.info("Registriere Winkhaus Door Dienste...")

    async def handle_set_mode(call: ServiceCall, mode: str) -> None:
        ent_reg = er.async_get(hass)
        for entity_id in call.data.get("entity_id", []):
            entry = ent_reg.async_get(entity_id)
            if entry and entry.config_entry_id in hass.data[DOMAIN]:
                data = hass.data[DOMAIN][entry.config_entry_id]
                client: DoorClient = data["client"]
                coordinator: DataUpdateCoordinator = data["coordinator"]
                await hass.async_add_executor_job(client.execute_command, "mode", mode)
                await coordinator.async_request_refresh()

    async def handle_set_day_mode(call: ServiceCall) -> None:
        await handle_set_mode(call, "day")

    async def handle_set_night_mode(call: ServiceCall) -> None:
        await handle_set_mode(call, "night")

    hass.services.async_register(DOMAIN, "set_day_mode", handle_set_day_mode)
    hass.services.async_register(DOMAIN, "set_night_mode", handle_set_night_mode)

def async_remove_services(hass: HomeAssistant) -> None:
    _LOGGER.info("Entferne Winkhaus Door Dienste.")
    hass.services.async_remove(DOMAIN, "set_day_mode")
    hass.services.async_remove(DOMAIN, "set_night_mode")