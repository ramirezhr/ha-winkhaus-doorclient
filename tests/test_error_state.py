"""The fault sensor as a declared enum.

An enum sensor rejects a state it did not declare, so the interesting case
is not the three known faults but the fourth one nobody has seen yet.
"""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.winkhaus_doorclient.const import DOMAIN
from custom_components.winkhaus_doorclient.sensor import (
    ERROR_STATES,
    KNOWN_FAULTS,
    UNKNOWN_FAULT,
)

SERIAL = "WH_01TEST123456"
API = "custom_components.winkhaus_doorclient.api.DoorClient"
ENTITY = f"sensor.winkhaus_door_{SERIAL.lower()}_error_state"


def states(error=...):
    base = [
        {"name": "state", "value": "closed"},
        {"name": "locked", "value": False},
        {"name": "mode", "value": "day"},
    ]
    if error is not ...:
        base.append({"name": "error", "value": error})
    return base


async def setup_reporting(hass: HomeAssistant, error=...):
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

    with patch(f"{API}.get_states", new_callable=AsyncMock, return_value=states(error)), patch(
        f"{API}.get_system_state", new_callable=AsyncMock,
        return_value={"firmware": "1.6.2_2607171112"},
    ), patch(f"{API}.get_configuration", new_callable=AsyncMock, return_value={}), patch(
        f"{API}.connect_and_monitor", new_callable=AsyncMock
    ), patch(f"{API}.stop", new_callable=AsyncMock):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


class TestDeclaration:
    def test_the_three_known_faults_are_declared(self):
        assert set(KNOWN_FAULTS) == {"blocked", "overcurrent", "batterylow"}

    def test_no_fault_and_a_catch_all_round_out_the_options(self):
        assert set(ERROR_STATES) == {"none", *KNOWN_FAULTS, UNKNOWN_FAULT}

    async def test_the_sensor_declares_itself_as_an_enum(self, hass):
        await setup_reporting(hass)
        state = hass.states.get(ENTITY)

        assert state.attributes["device_class"] == SensorDeviceClass.ENUM
        assert set(state.attributes["options"]) == set(ERROR_STATES)


class TestReportedFaults:
    async def test_no_fault(self, hass):
        await setup_reporting(hass)
        assert hass.states.get(ENTITY).state == "none"

    @pytest.mark.parametrize("fault", KNOWN_FAULTS)
    async def test_each_known_fault(self, hass, fault):
        await setup_reporting(hass, [fault])
        assert hass.states.get(ENTITY).state == fault

    async def test_the_first_of_several_becomes_the_state(self, hass):
        await setup_reporting(hass, ["blocked", "overcurrent"])
        state = hass.states.get(ENTITY)

        assert state.state == "blocked"
        assert state.attributes["all_errors"] == ["blocked", "overcurrent"]
        assert state.attributes["error_count"] == 2

    async def test_an_empty_list_means_no_fault(self, hass):
        await setup_reporting(hass, [])
        assert hass.states.get(ENTITY).state == "none"


class TestUnknownFault:
    """A firmware update could add a code nobody has seen."""

    async def test_it_does_not_invalidate_the_sensor(self, hass):
        await setup_reporting(hass, ["motor_overheated"])
        state = hass.states.get(ENTITY)

        assert state.state == UNKNOWN_FAULT
        assert state.state in state.attributes["options"]

    async def test_the_raw_code_stays_visible(self, hass):
        """Whoever reports the issue needs to see what the lock actually said."""
        await setup_reporting(hass, ["motor_overheated"])

        assert hass.states.get(ENTITY).attributes["all_errors"] == ["motor_overheated"]

    async def test_it_is_logged_so_it_can_be_added(self, hass, caplog):
        await setup_reporting(hass, ["motor_overheated"])

        assert "motor_overheated" in caplog.text
        assert "unknown fault" in caplog.text.lower()

    async def test_every_state_is_translated(self):
        """An undeclared state would show the user a raw key."""
        import json
        import pathlib

        base = pathlib.Path(__file__).parent.parent / "custom_components" / "winkhaus_doorclient"
        for datei in [base / "strings.json", base / "translations" / "de.json"]:
            zustaende = json.loads(datei.read_text(encoding="utf-8"))
            zustaende = zustaende["entity"]["sensor"]["error_state"]["state"]
            assert set(zustaende) == set(ERROR_STATES), f"mismatch in {datei.name}"
