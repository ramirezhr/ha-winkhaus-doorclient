"""What the lock entity is allowed to claim about the bolt.

The door sensor learned this lesson first, in issue #7: a field that is not
there must not turn into a confident answer. The lock guarded only the case
of no data at all, so a status answer that arrived *with* data but *without*
`locked` was read as `str(None).lower() == "true"` - False, which Home
Assistant shows as "unlocked". Of the two possible wrong answers for a door
lock, that is the worse one.

Home Assistant's `entity-unavailable` rule says the same thing from the other
side: if a fetch succeeds but single fields are missing, the state belongs on
unknown, not on a guess.
"""

from unittest.mock import AsyncMock, patch

from homeassistant.components.lock import LockState
from homeassistant.const import (
    CONF_IP_ADDRESS,
    CONF_PASSWORD,
    CONF_USERNAME,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.winkhaus_doorclient.const import DOMAIN

SERIAL = "WH_01TEST123456"
API = "custom_components.winkhaus_doorclient.api.DoorClient"
ENTITY = f"lock.winkhaus_door_{slugify(SERIAL)}_lock"


def states(locked=...):
    base = [
        {"name": "state", "value": "closed"},
        {"name": "mode", "value": "day"},
    ]
    if locked is not ...:
        base.append({"name": "locked", "value": locked})
    return base


async def setup_reporting(hass: HomeAssistant, locked=...):
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
        f"{API}.get_states", new_callable=AsyncMock, return_value=states(locked)
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


class TestAReportedBolt:
    async def test_locked(self, hass: HomeAssistant) -> None:
        await setup_reporting(hass, locked=True)
        assert hass.states.get(ENTITY).state == LockState.LOCKED

    async def test_unlocked(self, hass: HomeAssistant) -> None:
        await setup_reporting(hass, locked=False)
        assert hass.states.get(ENTITY).state == LockState.UNLOCKED

    async def test_the_field_may_arrive_as_text(self, hass: HomeAssistant) -> None:
        """Some firmware versions send "true" rather than a boolean."""
        await setup_reporting(hass, locked="true")
        assert hass.states.get(ENTITY).state == LockState.LOCKED


class TestAMissingField:
    async def test_a_missing_locked_field_is_unknown_not_unlocked(
        self, hass: HomeAssistant
    ) -> None:
        """The whole point: an absent bolt position is not an open door."""
        await setup_reporting(hass)

        assert hass.states.get(ENTITY).state == STATE_UNKNOWN

    async def test_an_explicit_none_is_unknown(self, hass: HomeAssistant) -> None:
        await setup_reporting(hass, locked=None)
        assert hass.states.get(ENTITY).state == STATE_UNKNOWN
