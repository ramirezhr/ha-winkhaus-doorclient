"""Setting up and unloading a config entry."""

import time
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.components.lock import LockState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.winkhaus_doorclient import (
    async_remove_config_entry_device,
)
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


LOCK_ENTITY = f"lock.winkhaus_door_{SERIAL.lower()}_lock"


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


async def test_lost_contact_is_wired_to_the_coordinator(
    hass, entry, reachable_lock
) -> None:
    """The client reports a total loss, the coordinator decides what it means.

    Split on purpose: the client would otherwise have to know the grace
    window, and a single failed fallback fetch would be enough to grey out
    every entity.
    """
    from custom_components.winkhaus_doorclient.coordinator import STALE_STATE_AFTER

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    client = coordinator.client
    assert client.on_contact_lost is not None

    # Fresh state: the report changes nothing.
    client.on_contact_lost()
    assert coordinator.last_update_success is True

    # Stale state: the same report takes the entities down.
    coordinator._last_good = time.time() - (STALE_STATE_AFTER + 10)
    client.on_contact_lost()
    assert coordinator.last_update_success is False


async def test_a_push_does_not_blank_the_fields_it_omits(
    hass, entry, reachable_lock
) -> None:
    """The real handler, not a copy of its logic.

    test_protocol covers the merge rules against a re-implementation, which
    would stay green if `handle_state_change` itself changed. This goes
    through the callback the client actually calls - and the lock entity
    depends on it: since a missing `locked` field reads as unknown, a push
    that replaced the set instead of merging into it would blank the bolt
    position on every state change.
    """
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    client = coordinator.client

    coordinator.async_set_updated_data(
        [
            {"name": "state", "value": "closed"},
            {"name": "locked", "value": True},
            {"name": "mode", "value": "day"},
            {"name": "time", "value": 1787486008},
        ]
    )

    # A real push, as measured: state, locked and mode - no "time".
    client.on_state_change(
        [
            {"name": "state", "value": "closed"},
            {"name": "locked", "value": False},
            {"name": "mode", "value": "day"},
        ]
    )

    values = {i["name"]: i["value"] for i in coordinator.data}
    assert values["locked"] is False          # the push wins
    assert values["time"] == 1787486008       # the omitted field survives
    assert hass.states.get(LOCK_ENTITY).state == LockState.UNLOCKED


class TestRemovingADevice:
    """Deleting a device row by hand.

    `stale-devices` asks for this when an integration cannot tell on its own
    that a device is gone. Here it never can: one entry is one lock, so
    nothing disappears behind the entry - but a replaced lock leaves its
    predecessor in the registry, because the serial number is part of the key
    material and the new one is a new device.
    """

    async def test_the_configured_lock_may_not_be_removed(
        self, hass, entry, reachable_lock
    ) -> None:
        """It would come back on the next reload - delete the entry instead."""
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, SERIAL)})
        assert device is not None

        assert await async_remove_config_entry_device(hass, entry, device) is False

    async def test_a_leftover_lock_may_be_removed(
        self, hass, entry, reachable_lock
    ) -> None:
        """The case the rule is actually about: a lock that was replaced."""
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        previous = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "WH_01OLDLOCK0000")},
            manufacturer="Winkhaus",
        )

        assert await async_remove_config_entry_device(hass, entry, previous) is True

