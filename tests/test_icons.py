"""Icons are defined in icons.json rather than in the code.

Icon translations are resolved by the frontend, so they never appear in an
entity's state attributes. What can be checked here is that the file loads,
that every key matches an entity that actually exists, and that the
state-dependent entries match the values those entities really report.
A key that matches nothing fails silently in production.
"""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.icon import async_get_icons
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.winkhaus_doorclient.const import (
    CONF_UPDATE_MODE,
    DOMAIN,
    MODE_POLLING,
)

SERIAL = "WH_01TEST123456"
API = "custom_components.winkhaus_doorclient.api.DoorClient"
PREFIX = f"winkhaus_door_{SERIAL.lower()}"

SYSTEM_STATE = {
    "firmware": "1.6.2_2607171112",
    "lock_cnt": 1458,
    "unlock_cnt": 227,
    "error_cnt": 10,
}


def states(mode="day"):
    return [
        {"name": "state", "value": "closed"},
        {"name": "locked", "value": False},
        {"name": "mode", "value": mode},
    ]


async def setup_with(hass: HomeAssistant, *, mode="day", options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "serial_number": SERIAL,
            CONF_IP_ADDRESS: "192.168.1.50",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
        },
        options=options or {},
        unique_id=SERIAL,
    )
    entry.add_to_hass(hass)

    with patch(f"{API}.get_states", return_value=states(mode)), patch(
        f"{API}.get_system_state", return_value=dict(SYSTEM_STATE)
    ), patch(f"{API}.get_configuration", return_value={}), patch(
        f"{API}.connect_and_monitor", new_callable=AsyncMock
    ), patch(f"{API}.stop", new_callable=AsyncMock):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def icons_of(hass: HomeAssistant) -> dict:
    loaded = await async_get_icons(hass, "entity", integrations=[DOMAIN])
    return loaded[DOMAIN]


async def test_icons_file_is_loaded(hass: HomeAssistant) -> None:
    await setup_with(hass)
    assert await icons_of(hass)


@pytest.mark.parametrize(
    ("platform", "key", "expected"),
    [
        ("button", "clear_errors", "mdi:shield-refresh"),
        ("select", "mode", "mdi:weather-sunny"),
        ("sensor", "lock_cnt", "mdi:lock-check"),
        ("sensor", "unlock_cnt", "mdi:lock-open-variant"),
        ("sensor", "error_cnt", "mdi:alert-circle-outline"),
        ("sensor", "error_state", "mdi:alert-circle-outline"),
        ("sensor", "connection_mode", "mdi:lan-connect"),
    ],
)
async def test_default_icons(hass: HomeAssistant, platform, key, expected) -> None:
    await setup_with(hass)
    icons = await icons_of(hass)
    assert icons[platform][key]["default"] == expected


async def test_every_key_belongs_to_a_real_entity(hass: HomeAssistant) -> None:
    """A key that matches no entity is dead weight nobody would notice."""
    await setup_with(hass)
    icons = await icons_of(hass)
    registry = er.async_get(hass)

    vorhanden = {e.entity_id for e in registry.entities.values()}
    for platform, eintraege in icons.items():
        for key in eintraege:
            erwartet = f"{platform}.{PREFIX}_{key}"
            assert erwartet in vorhanden, f"icons.json defines {platform}.{key}, which has no entity"


async def test_mode_state_key_matches_the_option(hass: HomeAssistant) -> None:
    """The night icon only appears if the key equals the option value."""
    await setup_with(hass, mode="night")
    icons = await icons_of(hass)

    zustand = hass.states.get(f"select.{PREFIX}_mode").state
    assert zustand == "night"
    assert icons["select"]["mode"]["state"][zustand] == "mdi:weather-night"


async def test_connection_mode_state_key_matches(hass: HomeAssistant) -> None:
    """This sensor reports "Polling" capitalised, so the key must be too."""
    await setup_with(hass, options={CONF_UPDATE_MODE: MODE_POLLING})
    icons = await icons_of(hass)

    zustand = hass.states.get(f"sensor.{PREFIX}_connection_mode").state
    assert zustand == "Polling"
    assert icons["sensor"]["connection_mode"]["state"][zustand] == "mdi:cached"


async def test_no_icons_left_in_the_code(hass: HomeAssistant) -> None:
    """The point of the exercise: nothing hard-codes an icon any more."""
    import pathlib

    base = pathlib.Path(__file__).parent.parent / "custom_components" / "winkhaus_doorclient"
    offenders = [
        f.name
        for f in base.glob("*.py")
        if "_attr_icon" in f.read_text(encoding="utf-8")
    ]
    assert not offenders, f"icons still defined in code: {offenders}"
