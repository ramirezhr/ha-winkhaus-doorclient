# in custom_components/winkhaus_doorclient/coordinator.py

"""Coordinators and the runtime data carried on the config entry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DoorClient, create_legacy_ssl_context
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_UPDATE_MODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MODE_HYBRID,
)

_LOGGER = logging.getLogger(__name__)


async def async_create_client(
    hass: HomeAssistant,
    *,
    serial_number: str,
    ip: str,
    password: str,
    username: str = "admin",
) -> DoorClient:
    """Build a client on Home Assistant's shared HTTP session.

    Reusing that session is the documented approach and keeps connection
    pooling in one place. The SSL context is built in an executor because
    loading the trust store touches the file system.
    """
    return DoorClient(
        serial_number=serial_number,
        ip=ip,
        password=password,
        username=username,
        session=async_get_clientsession(hass),
        ssl_context=await hass.async_add_executor_job(create_legacy_ssl_context),
    )

# In hybrid mode the WebSocket delivers changes as they happen, so HTTP only
# has to act as a safety net.
HYBRID_SCAN_INTERVAL = 120

# The system state changes rarely - firmware, model and lifetime counters.
SYSTEM_SCAN_INTERVAL = timedelta(hours=12)

# How many failed updates before the user is told something is wrong.
MAX_FAILURES_BEFORE_ALERT = 3


class WinkhausCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Polls the lock's state and keeps it available across outages."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: DoorClient
    ) -> None:
        self.client = client
        self.serial = entry.data.get("serial_number", "Unknown")
        self._consecutive_failures = 0
        self._unavailable_logged = False

        update_mode = entry.options.get(CONF_UPDATE_MODE, MODE_HYBRID)
        if update_mode == MODE_HYBRID:
            interval = HYBRID_SCAN_INTERVAL
            _LOGGER.info(f"[{self.serial}] Starting in HYBRID mode (WebSockets active)")
        else:
            interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            _LOGGER.info(
                f"[{self.serial}] Starting in POLLING mode (interval: {interval}s)"
            )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.serial}",
            update_interval=timedelta(seconds=interval),
        )

    @property
    def consecutive_failures(self) -> int:
        """Failed updates since the last successful one."""
        return self._consecutive_failures

    @property
    def issue_id(self) -> str:
        return f"unreachable_{self.serial}"

    async def _async_update_data(self) -> list[dict[str, Any]]:
        _LOGGER.debug(f"[COORDINATOR {self.serial}] Starting status query...")

        try:
            new_data = await self.client.get_states()
        except Exception as err:
            return self._handle_failure(err)

        if self._consecutive_failures:
            _LOGGER.info(
                f"[COORDINATOR {self.serial}] Connection restored after "
                f"{self._consecutive_failures} failures"
            )
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            self._consecutive_failures = 0
            self._unavailable_logged = False

        return new_data

    def _handle_failure(self, err: Exception) -> list[dict[str, Any]]:
        """Decide what a failed update means.

        Returns the cached data when there is any, because for a door lock a
        status from a few minutes ago beats a greyed-out card. Raises only
        when nothing was ever fetched, so Home Assistant can retry the setup.
        """
        error_msg = str(err)

        if "401" in error_msg or "Authentication failed" in error_msg:
            _LOGGER.error(
                f"[COORDINATOR {self.serial}] Authentication failed - triggering reauth"
            )
            raise ConfigEntryAuthFailed("Authentication failed") from err

        self._consecutive_failures += 1

        # Home Assistant types data as always present, but before the first
        # successful refresh it is None. An empty list is just as unusable,
        # so both are treated the same.
        if not self.data:
            _LOGGER.error(
                f"[COORDINATOR {self.serial}] Initial setup failed, "
                f"no cached data available: {error_msg}"
            )
            raise UpdateFailed(
                f"Failed to communicate with device: {error_msg}"
            ) from err

        # Log the outage once instead of on every attempt. A lock that is
        # away for an hour would otherwise write thirty identical warnings.
        if not self._unavailable_logged:
            _LOGGER.warning(
                f"[COORDINATOR {self.serial}] Update failed ({error_msg}), "
                f"keeping previous data. Further failures stay silent until "
                f"the lock answers again."
            )
            self._unavailable_logged = True
        else:
            _LOGGER.debug(
                f"[COORDINATOR {self.serial}] Still unreachable "
                f"(failure #{self._consecutive_failures}): {error_msg}"
            )

        if self._consecutive_failures == MAX_FAILURES_BEFORE_ALERT:
            self._raise_unreachable_issue()

        return self.data

    def _raise_unreachable_issue(self) -> None:
        """Report the outage in the Repairs dashboard.

        A repair issue rather than a notification: it survives a restart and
        points at the reconfigure flow, which is where a changed IP address
        is actually fixed.
        """
        _LOGGER.error(
            f"[COORDINATOR {self.serial}] {self._consecutive_failures} consecutive "
            f"failures! Device may be offline or unreachable."
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="device_unreachable",
            translation_placeholders={
                "serial": self.serial,
                "failures": str(self._consecutive_failures),
            },
        )


class WinkhausSystemCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches firmware, model and lifetime counters twice a day."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: DoorClient
    ) -> None:
        self.client = client
        self.serial = entry.data.get("serial_number", "Unknown")

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.serial}_system",
            update_interval=SYSTEM_SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug(
            f"[SYSTEM COORDINATOR {self.serial}] Loading system and config data "
            f"(12h interval)..."
        )
        try:
            sys_data = await self.client.get_system_state()
        except Exception as err:
            raise UpdateFailed(f"Error fetching system state: {err}") from err

        # The configuration endpoint only supplies the display name. Losing it
        # is not worth failing the whole update over.
        try:
            conf_data = await self.client.get_configuration()
        except Exception as conf_err:
            _LOGGER.warning(f"[{self.serial}] Could not load configuration: {conf_err}")
            conf_data = {}

        sys_data["_config"] = conf_data
        return sys_data


@dataclass
class WinkhausRuntimeData:
    """Everything the platforms need, kept on the entry itself.

    Home Assistant clears runtime_data when an entry is unloaded, so there
    is no bookkeeping in hass.data to keep in sync.
    """

    client: DoorClient
    coordinator: WinkhausCoordinator
    system_coordinator: WinkhausSystemCoordinator
    device_info: DeviceInfo


type WinkhausConfigEntry = ConfigEntry[WinkhausRuntimeData]
