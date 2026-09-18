"""Identity of every entity, pinned against accidental change.

A changed unique id orphans the registry entry of every existing
installation and silently creates a duplicate. A changed entity id breaks
automations. Neither shows up as an error, so both are pinned here.
"""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.winkhaus_doorclient.const import DOMAIN

SERIAL = "WH_01TEST123456"
API = "custom_components.winkhaus_doorclient.api.DoorClient"
PREFIX = f"winkhaus_door_{SERIAL.lower()}"

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
}

# entity_id suffix -> unique_id suffix, as shipped today.
# The two differ in places for historical reasons; that is the point.
EXPECTED = {
    f"lock.{PREFIX}_lock": SERIAL,
    f"select.{PREFIX}_mode": f"{SERIAL}_mode",
    f"binary_sensor.{PREFIX}_door": f"{SERIAL}_door_state",
    f"button.{PREFIX}_clear_errors": f"{SERIAL}_unblock",
    f"sensor.{PREFIX}_lock_cnt": f"{SERIAL}_lock_cnt",
    f"sensor.{PREFIX}_unlock_cnt": f"{SERIAL}_unlock_cnt",
    f"sensor.{PREFIX}_error_cnt": f"{SERIAL}_error_cnt",
    f"sensor.{PREFIX}_connection_mode": f"{SERIAL}_connection_mode",
    f"sensor.{PREFIX}_error_state": f"{SERIAL}_error_state",
}


@pytest.fixture
async def loaded(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "serial_number": SERIAL,
            CONF_IP_ADDRESS: "192.168.1.50",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
        },
        unique_id=SERIAL,
    )
    entry.add_to_hass(hass)

    with patch(f"{API}.get_states", return_value=STATES), patch(
        f"{API}.get_system_state", return_value=dict(SYSTEM_STATE)
    ), patch(f"{API}.get_configuration", return_value={"system": {"name": "Front Door"}}), patch(
        f"{API}.connect_and_monitor", new_callable=AsyncMock
    ), patch(f"{API}.stop", new_callable=AsyncMock):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


async def test_all_entities_exist(hass: HomeAssistant, loaded) -> None:
    registry = er.async_get(hass)
    missing = [e for e in EXPECTED if not registry.async_get(e)]
    assert not missing, f"missing entities: {missing}"


@pytest.mark.parametrize(("entity_id", "unique_id"), sorted(EXPECTED.items()))
async def test_identity_is_unchanged(
    hass: HomeAssistant, loaded, entity_id, unique_id
) -> None:
    """Both ids are contracts with existing installations."""
    registry = er.async_get(hass)
    record = registry.async_get(entity_id)

    assert record is not None, f"{entity_id} was not created"
    assert record.unique_id == unique_id


async def test_no_unexpected_entities(hass: HomeAssistant, loaded) -> None:
    """A new entity should be a conscious decision, not a side effect."""
    registry = er.async_get(hass)
    created = {
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, loaded.entry_id)
    }

    assert created == set(EXPECTED)


async def test_every_entity_belongs_to_the_device(hass: HomeAssistant, loaded) -> None:
    registry = er.async_get(hass)
    for entity_id in EXPECTED:
        record = registry.async_get(entity_id)
        assert record.device_id is not None, f"{entity_id} has no device"
