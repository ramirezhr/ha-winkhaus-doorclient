# in custom_components/winkhaus_doorclient/__init__.py

import asyncio
import logging

from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from typing import Any

from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import (
    WinkhausConfigEntry,
    WinkhausCoordinator,
    WinkhausRuntimeData,
    WinkhausSystemCoordinator,
    async_create_client,
)
from .const import CONF_UPDATE_MODE, MODE_HYBRID

PLATFORMS = ["lock", "select", "binary_sensor", "sensor", "button"]

# Fields the lock omits entirely instead of reporting an empty value. Their
# absence is the message, so they must never be carried over from a previous
# payload when merging a partial push.
TRANSIENT_KEYS = {"error"}

_LOGGER = logging.getLogger(__name__)


def _build_device_info(
    serial: str, sys_data: dict[str, Any]
) -> tuple[DeviceInfo, str | None]:
    """Describe the lock for the device registry."""
    config_data = sys_data.get("_config", {})

    # Read the user-defined name from {"system": {"name": "Front Door"}}.
    # Guard every level: the endpoint may be missing, empty or shaped
    # differently on older firmware.
    lock_name = None
    if isinstance(config_data, dict):
        system_cfg = config_data.get("system")
        if isinstance(system_cfg, dict):
            lock_name = system_cfg.get("name")

    raw_firmware = sys_data.get("firmware", "Unknown")
    if isinstance(raw_firmware, str) and "_" in raw_firmware:
        parts = raw_firmware.split("_")
        sw_version = f"{parts[0]} ({parts[1]})" if len(parts) >= 2 else raw_firmware
    else:
        sw_version = raw_firmware

    info = DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=lock_name if lock_name else f"Winkhaus Door ({serial})",
        manufacturer="Winkhaus",
        model="EAV4+" if serial.startswith("WH_01") else "blueMotion+",
        sw_version=sw_version,
    )
    return info, lock_name


async def async_setup_entry(hass: HomeAssistant, entry: WinkhausConfigEntry) -> bool:
    serial = entry.data.get("serial_number", "Unknown")

    client = await async_create_client(
        hass,
        serial_number=serial,
        ip=entry.data[CONF_IP_ADDRESS],
        password=entry.data[CONF_PASSWORD],
        username=entry.data[CONF_USERNAME],
    )

    coordinator = WinkhausCoordinator(hass, entry, client)
    system_coordinator = WinkhausSystemCoordinator(hass, entry, client)

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
        # data is None before the first successful refresh; an empty
        # list is just as unusable.
        if not coordinator.data:
            _LOGGER.error(f"[COORDINATOR {serial}] No cached data available. Aborting.")
            return False

    def handle_state_change(new_states: list[dict[str, Any]]) -> None:
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

    if entry.options.get(CONF_UPDATE_MODE, MODE_HYBRID) == MODE_HYBRID:

        async def start_ws_delayed() -> None:
            await asyncio.sleep(2)
            _LOGGER.debug(f"[WS PUSH {serial}] Starting WebSocket monitoring...")
            await client.connect_and_monitor()

        entry.async_create_background_task(
            hass, start_ws_delayed(), name=f"winkhaus_ws_{serial}"
        )

    device_info, lock_name = _build_device_info(serial, system_coordinator.data or {})

    # Keep the config entry title in sync with the name configured on the
    # lock, so the integration list shows the same name as the device.
    if lock_name and entry.title != lock_name:
        hass.config_entries.async_update_entry(entry, title=lock_name)

    entry.runtime_data = WinkhausRuntimeData(
        client=client,
        coordinator=coordinator,
        system_coordinator=system_coordinator,
        device_info=device_info,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, entry: WinkhausConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WinkhausConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        _LOGGER.debug(
            f"Closing active connections (WebSocket/Watchdog) for {entry.entry_id}..."
        )
        await entry.runtime_data.client.stop()

        _LOGGER.debug("Waiting 2 seconds to let the lock release its sockets...")
        await asyncio.sleep(2)

        # An unreachable-device issue must not outlive the entry it belongs
        # to, or it lingers in the Repairs dashboard with nothing to fix.
        serial = entry.data.get("serial_number", "Unknown")
        ir.async_delete_issue(hass, DOMAIN, f"unreachable_{serial}")

    return unload_ok
