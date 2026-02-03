# in custom_components/winkhaus_doorclient/__init__.py

from datetime import timedelta
import logging
import json

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed
from .api import DoorClient
from .const import DOMAIN

PLATFORMS = ["lock", "select", "binary_sensor"]
_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    serial = entry.data.get("serial_number", "Unknown")

    client = await hass.async_add_executor_job(
        lambda: DoorClient(
            serial_number=serial,
            ip=entry.data[CONF_IP_ADDRESS],
            password=entry.data[CONF_PASSWORD],
            username=entry.data[CONF_USERNAME]
        )
    )

    consecutive_failures = 0
    MAX_FAILURES_BEFORE_ALERT = 3

    async def async_update_data():
        nonlocal consecutive_failures
        
        _LOGGER.debug(f"[COORDINATOR {serial}] Starting status query...")
        
        try:
            new_data = await hass.async_add_executor_job(client.get_states)
            
            if consecutive_failures > 0:
                _LOGGER.info(f"[COORDINATOR {serial}] Connection restored after {consecutive_failures} failures")
                consecutive_failures = 0
            
            return new_data
            
        except Exception as err:
            error_msg = str(err)
            error_type = type(err).__name__
            
            if "401" in error_msg or "Authentication failed" in error_msg:
                _LOGGER.error(f"[COORDINATOR {serial}] Authentication failed - triggering reauth")
                raise ConfigEntryAuthFailed("Authentication failed") from err
            
            consecutive_failures += 1
            
            if coordinator.data is not None:
                _LOGGER.warning(
                    f"[COORDINATOR {serial}] Update failed ({error_type}), "
                    f"keeping previous data (failure #{consecutive_failures})"
                )
                
                if consecutive_failures >= MAX_FAILURES_BEFORE_ALERT:
                    _LOGGER.error(
                        f"[COORDINATOR {serial}] {consecutive_failures} consecutive failures! "
                        f"Device may be offline or unreachable."
                    )
                
                return coordinator.data
            
            _LOGGER.error(
                f"[COORDINATOR {serial}] Initial setup failed, no cached data available: {error_type}"
            )
            raise UpdateFailed(f"Failed to communicate with device: {error_type}") from err

    coordinator_name = f"{DOMAIN}_{serial}"

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=coordinator_name,
        update_method=async_update_data,
        update_interval=timedelta(seconds=60),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except UpdateFailed as err:
        _LOGGER.error(f"[COORDINATOR {serial}] Failed to set up device: {err}")
        return False

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok