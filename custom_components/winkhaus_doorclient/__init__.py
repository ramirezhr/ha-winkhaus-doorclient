# in custom_components/winkhaus_doorclient/__init__.py

from datetime import timedelta
import logging
import asyncio
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed
from .api import DoorClient
from .const import DOMAIN, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, CONF_UPDATE_MODE, MODE_HYBRID, MODE_POLLING

PLATFORMS = ["lock", "select", "binary_sensor", "sensor", "button"]
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
    
    update_mode = entry.options.get(CONF_UPDATE_MODE, MODE_HYBRID)
    
    if update_mode == MODE_HYBRID:
        scan_interval_seconds = 120
        _LOGGER.info(f"[{serial}] Starte im HYBRID-Modus (WebSockets aktiv)")
    else:
        scan_interval_seconds = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        _LOGGER.info(f"[{serial}] Starte im POLLING-Modus (Intervall: {scan_interval_seconds}s)")
    
    consecutive_failures = 0
    MAX_FAILURES_BEFORE_ALERT = 3

    async def async_update_data():
        nonlocal consecutive_failures
        
        _LOGGER.debug(f"[COORDINATOR {serial}] Starting status query...")
        
        try:
            new_data = await hass.async_add_executor_job(client.get_states)
            
            if consecutive_failures > 0:
                _LOGGER.info(f"[COORDINATOR {serial}] Connection restored after {consecutive_failures} failures")
                
                hass.components.persistent_notification.async_dismiss(
                notification_id=f"winkhaus_{serial}_offline"
                )
                
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
                
                    hass.components.persistent_notification.async_create(
                        title="⚠️ Winkhaus Door Connection Issue",
                        message=(
                            f"Your Winkhaus door ({serial}) has been unreachable for "
                            f"{consecutive_failures} consecutive updates (≈{consecutive_failures} minutes).\n\n"
                            f"Please check:\n"
                            f"• Is the device powered on?\n"
                            f"• Is the network connection stable?\n"
                            f"• Can you ping the device from Home Assistant?"
                        ),
                        notification_id=f"winkhaus_{serial}_offline"
                    )
                
                return coordinator.data
            
            _LOGGER.error(
                f"[COORDINATOR {serial}] Initial setup failed, no cached data available: {error_type}"
            )
            raise UpdateFailed(f"Failed to communicate with device: {error_type}") from err


    async def async_update_system_data():
        _LOGGER.debug(f"[SYSTEM COORDINATOR {serial}] Loading system status (12h interval)...")
        try:
            return await hass.async_add_executor_job(client.get_system_state)
        except Exception as err:
            raise UpdateFailed(f"Error fetching system state: {err}") from err
            
    coordinator_name = f"{DOMAIN}_{serial}"
    
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{serial}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=scan_interval_seconds),
    )

    system_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{serial}_system",
        update_method=async_update_system_data,
        update_interval=timedelta(hours=12),
    )

    try:
        _LOGGER.debug(f"[{serial}] Initialer HTTP-Refresh gestartet...")
        await coordinator.async_config_entry_first_refresh()
        await system_coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except Exception as err:
        _LOGGER.error(f"[COORDINATOR {serial}] Initialer Start fehlgeschlagen: {err}")
        if coordinator.data is None:
            _LOGGER.error(f"[COORDINATOR {serial}] Keine Cached-Daten verfügbar. Abbruch.")
            return False
            
    def handle_state_change(new_states):
        _LOGGER.debug(f"[PUSH {serial}] Sofortiges Update empfangen: {new_states}")
        coordinator.async_set_updated_data(new_states)

    client.on_state_change = handle_state_change

    if update_mode == MODE_HYBRID:
        async def start_ws_delayed():
            await asyncio.sleep(2)
            _LOGGER.debug(f"[WS PUSH {serial}] Starte WebSocket Überwachung...")
            await client.connect_and_monitor()

        entry.async_create_background_task(
            hass, 
            start_ws_delayed(), 
            name=f"winkhaus_ws_{serial}"
        )  


    sys_data = system_coordinator.data or {}
    
    if serial.startswith("WH_01"):
        model_name = "EAV4+"
    else:
        model_name = "blueMotion+"

    raw_firmware = sys_data.get("firmware", "Unknown")
    if isinstance(raw_firmware, str) and "_" in raw_firmware:
        parts = raw_firmware.split("_")
        if len(parts) >= 2:
            sw_version = f"{parts[0]} ({parts[1]})"
        else:
            sw_version = raw_firmware
    else:
        sw_version = raw_firmware
        
    device_info = {
        "identifiers": {(DOMAIN, serial)},
        "name": f"Winkhaus Door ({serial})",
        "manufacturer": "Winkhaus",
        "model": model_name,
        "sw_version": sw_version,
    }

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "system_coordinator": system_coordinator,
        "device_info": device_info,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)
    

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        entry_data = hass.data[DOMAIN].get(entry.entry_id)
        
        if entry_data and "client" in entry_data:
            _LOGGER.debug(f"Trenne aktive Verbindungen (WebSocket/Watchdog) für {entry.entry_id}...")
            await entry_data["client"].stop()
            
            _LOGGER.debug("Warte 2 Sekunden, damit das Schloss Sockets freigeben kann...")
            await asyncio.sleep(2)
            # ------------------------------------------

        hass.data[DOMAIN].pop(entry.entry_id)
        
    return unload_ok