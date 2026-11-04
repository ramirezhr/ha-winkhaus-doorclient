"""The door sensor, and what it is allowed to claim.

A binary sensor has two states and no room for doubt, which is exactly the
problem: a lock that does not report a door position still has to produce an
answer. Issue #7 showed what that cost. On an EAV4+ the door contact is
optional, and the lock answers "nocontact" when no magnet clip is fitted or
declared; the comparison against "open" turned that into False, and the
dashboard showed a closed door for an installation that cannot tell. Unknown
is the only honest answer there.
"""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import (
    CONF_IP_ADDRESS,
    CONF_PASSWORD,
    CONF_USERNAME,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.winkhaus_doorclient.binary_sensor import DOOR_STATES
from custom_components.winkhaus_doorclient.const import DOMAIN

SERIAL = "WH_01TEST123456"
API = "custom_components.winkhaus_doorclient.api.DoorClient"
ENTITY = f"binary_sensor.winkhaus_door_{SERIAL.lower()}_door"


def states(door=...):
    base = [
        {"name": "locked", "value": False},
        {"name": "mode", "value": "day"},
    ]
    if door is not ...:
        base.append({"name": "state", "value": door})
    return base


async def setup_reporting(hass: HomeAssistant, door=...):
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

    with patch(
        f"{API}.get_states", new_callable=AsyncMock, return_value=states(door)
    ), patch(
        f"{API}.get_system_state",
        new_callable=AsyncMock,
        return_value={"firmware": "1.6.2_2607171112"},
    ), patch(
        f"{API}.get_configuration", new_callable=AsyncMock, return_value={}
    ), patch(
        f"{API}.connect_and_monitor", new_callable=AsyncMock
    ), patch(
        f"{API}.stop", new_callable=AsyncMock
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


class TestAWiredContact:
    async def test_open(self, hass: HomeAssistant) -> None:
        await setup_reporting(hass, door="open")
        assert hass.states.get(ENTITY).state == STATE_ON

    async def test_closed(self, hass: HomeAssistant) -> None:
        await setup_reporting(hass, door="closed")
        assert hass.states.get(ENTITY).state == STATE_OFF

    @pytest.mark.parametrize("door", ["OPEN", "Closed"])
    async def test_case_does_not_matter(self, hass: HomeAssistant, door) -> None:
        """The field has arrived capitalised from some firmware versions."""
        await setup_reporting(hass, door=door)
        assert hass.states.get(ENTITY).state in (STATE_ON, STATE_OFF)


class TestNoContact:
    async def test_nocontact_is_unknown_not_closed(self, hass: HomeAssistant) -> None:
        """Issue #7: an EAV4+ with no magnet clip reported a closed door."""
        await setup_reporting(hass, door="nocontact")

        assert hass.states.get(ENTITY).state == STATE_UNKNOWN

    async def test_a_missing_field_is_unknown(self, hass: HomeAssistant) -> None:
        await setup_reporting(hass)
        assert hass.states.get(ENTITY).state == STATE_UNKNOWN

    @pytest.mark.parametrize("door", ["unknown", 99, "", None])
    async def test_anything_unrecognised_is_unknown(
        self, hass: HomeAssistant, door
    ) -> None:
        """A value nobody has seen yet must not become a confident answer."""
        await setup_reporting(hass, door=door)
        assert hass.states.get(ENTITY).state == STATE_UNKNOWN


class TestTheMapping:
    def test_only_the_two_reported_positions_are_known(self) -> None:
        assert DOOR_STATES == {"open": True, "closed": False}
