"""Setting up and unloading a config entry."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.winkhaus_doorclient.const import DOMAIN
from custom_components.winkhaus_doorclient.coordinator import WinkhausRuntimeData

SERIAL = "WH_01TEST123456"
API = "custom_components.winkhaus_doorclient.api.DoorClient"

STATES = [
    {"name": "state", "value": "closed"},
    {"name": "locked", "value": False},
    {"name": "mode", "value": "day"},
    {"name": "time", "value": 1787486008},
]

SYSTEM_STATE = {
    "firmware": "1.6.2_2607171112",
    "lock_cnt": 1458,
    "unlock_cnt": 227,
    "error_cnt": 10,
    "version": "BM+71003",
}


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    item = MockConfigEntry(
        domain=DOMAIN,
        title=f"Winkhaus Door ({SERIAL})",
        data={
            "serial_number": SERIAL,
            CONF_IP_ADDRESS: "192.168.1.50",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
        },
        unique_id=SERIAL,
    )
    item.add_to_hass(hass)
    return item


@pytest.fixture
def reachable_lock():
    """A lock that answers, without any network access."""
    with patch(f"{API}.get_states", return_value=STATES), patch(
        f"{API}.get_system_state", return_value=dict(SYSTEM_STATE)
    ), patch(f"{API}.get_configuration", return_value={"system": {"name": "Front Door"}}), patch(
        f"{API}.connect_and_monitor", new_callable=AsyncMock
    ), patch(f"{API}.stop", new_callable=AsyncMock) as stop:
        yield stop


async def test_setup_stores_runtime_data(hass, entry, reachable_lock) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, WinkhausRuntimeData)
    assert entry.runtime_data.coordinator.data == STATES


async def test_setup_does_not_use_hass_data(hass, entry, reachable_lock) -> None:
    """Regression guard: everything lives on the entry now."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert DOMAIN not in hass.data


async def test_device_name_comes_from_the_lock(hass, entry, reachable_lock) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.device_info["name"] == "Front Door"
    assert entry.runtime_data.device_info["sw_version"] == "1.6.2 (2607171112)"


async def test_entities_are_created(hass, entry, reachable_lock) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    lock = hass.states.get(f"lock.winkhaus_door_{SERIAL.lower()}_lock")
    assert lock is not None
    assert lock.attributes["mode"] == "day"


async def test_unload_stops_the_client(hass, entry, reachable_lock) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    reachable_lock.assert_awaited_once()


async def test_setup_retries_when_lock_is_unreachable(hass, entry) -> None:
    """ConfigEntryNotReady must reach Home Assistant, not be swallowed.

    Regression: 2.4.1 - a broad except turned the retry signal into a
    permanent setup failure.
    """
    with patch(f"{API}.get_states", side_effect=Exception("Network error")):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
