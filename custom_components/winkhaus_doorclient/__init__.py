# in custom_components/winkhaus_doorclient/__init__.py

from datetime import timedelta
import logging
import asyncio
from homeassistant.config_entries import ConfigEntry
from homeassistant.components import persistent_notification
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from .api import DoorClient
from .const import DOMAIN, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, CONF_UPDATE_MODE, MODE_HYBRID, MODE_POLLING

PLATFORMS = ["lock", "select", "binary_sensor", "sensor", "button"]

# Fields the lock omits entirely instead of reporting an empty value. Their
# absence is the message, so they must never be carried over from a previous
# payload when merging a partial push.
TRANSIENT_KEYS = {"error"}

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
        _LOGGER.info(f"[{serial}] Starting in HYBRID mode (WebSockets active)")
    else:
        scan_interval_seconds = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        _LOGGER.info(f"[{serial}] Starting in POLLING mode (interval: {scan_interval_seconds}s)")
    
    consecutive_failures = 0
    MAX_FAILURES_BEFORE_ALERT = 3

    async def async_update_data():
        nonlocal consecutive_failures
        
        _LOGGER.debug(f"[COORDINATOR {serial}] Starting status query...")
        
        try:
            new_data = await hass.async_add_executor_job(client.get_states)
            
            if consecutive_failures > 0:
                _LOGGER.info(f"[COORDINATOR {serial}] Connection restored after {consecutive_failures} failures")
                
                persistent_notification.async_dismiss(
                    hass, f"winkhaus_{serial}_offline"
                )
                
                consecutive_failures = 0
            
            return new_data
            
        except Exception as err:
            error_msg = str(err)
            
            if "401" in error_msg or "Authentication failed" in error_msg:
                _LOGGER.error(f"[COORDINATOR {serial}] Authentication failed - triggering reauth")
                raise ConfigEntryAuthFailed("Authentication failed") from err
            
            consecutive_failures += 1
            
            if coordinator.data is not None:
                _LOGGER.warning(
                    f"[COORDINATOR {serial}] Update failed ({error_msg}), "
                    f"keeping previous data (failure #{consecutive_failures})"
                )
                
                if consecutive_failures >= MAX_FAILURES_BEFORE_ALERT:
                    _LOGGER.error(
                        f"[COORDINATOR {serial}] {consecutive_failures} consecutive failures! "
                        f"Device may be offline or unreachable."
                    )
                
                    persistent_notification.async_create(
                        hass,
                        title="Winkhaus Door Connection Issue",
                        message=(
                            f"Your Winkhaus door ({serial}) has been unreachable for "
                            f"{consecutive_failures} consecutive updates.\n\n"
                            f"Please check:\n"
                            f"- Is the device powered on?\n"
                            f"- Is the network connection stable?\n"
                            f"- Can you ping the device from Home Assistant?"
                        ),
                        notification_id=f"winkhaus_{serial}_offline"
                    )
                
                return coordinator.data
            
            _LOGGER.error(
                f"[COORDINATOR {serial}] Initial setup failed, no cached data available: {error_msg}"
            )
            raise UpdateFailed(f"Failed to communicate with device: {error_msg}") from err


    async def async_update_system_data():
        _LOGGER.debug(f"[SYSTEM COORDINATOR {serial}] Loading system and config data (12h interval)...")
        try:
            sys_data = await hass.async_add_executor_job(client.get_system_state)        
            try:
                conf_data = await hass.async_add_executor_job(client.get_configuration)
            except Exception as conf_err:
                _LOGGER.warning(f"[{serial}] Could not load configuration: {conf_err}")
                conf_data = {}
            sys_data["_config"] = conf_data
            return sys_data
            
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
        _LOGGER.debug(f"[{serial}] Initial HTTP refresh started...")
        await coordinator.async_config_entry_first_refresh()
        await system_coordinator.async_config_entry_first_refresh()
    except (ConfigEntryAuthFailed, ConfigEntryNotReady):
        # Both are Home Assistant's own retry signals: AuthFailed starts the
        # reauth flow, NotReady schedules another setup attempt with backoff.
        # Swallowing NotReady turned "try again shortly" into a permanent
        # failure - which is exactly what happened when the lock was not yet
        # reachable while Home Assistant was still booting.
        raise
    except Exception as err:
        _LOGGER.error(f"[COORDINATOR {serial}] Initial startup failed: {err}")
        if coordinator.data is None:
            _LOGGER.error(f"[COORDINATOR {serial}] No cached data available. Aborting.")
            return False
            
    def handle_state_change(new_states):
        _LOGGER.debug(f"[PUSH {serial}] Instant update received: {new_states}")

        # A state-change push carries only the fields that changed, while a
        # full poll also carries e.g. "time". Replacing the whole set would
        # make those fields vanish until the next poll, so merge instead.
        #
        # TRANSIENT_KEYS are exempt: the lock omits them rather than sending
        # an empty value, so inheriting a previous value would keep a cleared
        # fault alive forever.
        merged = {
            item["name"]: item["value"]
            for item in (coordinator.data or [])
            if item["name"] not in TRANSIENT_KEYS
        }
        merged.update({item["name"]: item["value"] for item in new_states})

        coordinator.async_set_updated_data(
            [{"name": key, "value": value} for key, value in merged.items()]
        )

    client.on_state_change = handle_state_change

    if update_mode == MODE_HYBRID:
        async def start_ws_delayed():
            await asyncio.sleep(2)
            _LOGGER.debug(f"[WS PUSH {serial}] Starting WebSocket monitoring...")
            await client.connect_and_monitor()

        entry.async_create_background_task(
            hass, 
            start_ws_delayed(), 
            name=f"winkhaus_ws_{serial}"
        )  


    sys_data = system_coordinator.data or {}
    config_data = sys_data.get("_config", {})
    
    # Read the user-defined name from {"system": {"name": "Front Door"}}.
    # Guard every level: the endpoint may be missing, empty or shaped
    # differently on older firmware.
    lock_name = None
    if isinstance(config_data, dict):
        system_cfg = config_data.get("system")
        if isinstance(system_cfg, dict):
            lock_name = system_cfg.get("name")
    
    # Fall back to the serial number if no name is set or the API failed
    device_name = lock_name if lock_name else f"Winkhaus Door ({serial})"
    
    # Hardware model detection based on the serial number prefix
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
        "name": device_name,
        "manufacturer": "Winkhaus",
        "model": model_name,
        "sw_version": sw_version,
    }

    # Keep the config entry title in sync with the name configured on the
    # lock, so the integration list shows the same name as the device.
    if lock_name and entry.title != lock_name:
        hass.config_entries.async_update_entry(entry, title=lock_name)

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
            _LOGGER.debug(f"Closing active connections (WebSocket/Watchdog) for {entry.entry_id}...")
            await entry_data["client"].stop()
            
            _LOGGER.debug("Waiting 2 seconds to let the lock release its sockets...")
            await asyncio.sleep(2)
            # ------------------------------------------

        hass.data[DOMAIN].pop(entry.entry_id)
        
    return unload_ok