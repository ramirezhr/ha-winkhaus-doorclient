"""The diagnostics export ends up in public issue reports.

Two things matter: nothing identifying leaks, and enough is left to actually
diagnose something.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.winkhaus_doorclient.const import DOMAIN
from custom_components.winkhaus_doorclient.diagnostics import (
    async_get_config_entry_diagnostics,
)

SERIAL = "WH_01TEST123456"
PASSWORD = "hunter2-very-secret"
IP = "10.10.30.197"
API = "custom_components.winkhaus_doorclient.api.DoorClient"

STATES = [
    {"name": "state", "value": "closed"},
    {"name": "locked", "value": False},
    {"name": "mode", "value": "day"},
]

SYSTEM_STATE = {
    "firmware": "1.6.2_2607171112",
    "battery": 0,
    "lock_cnt": 1458,
    "unlock_cnt": 227,
    "error_cnt": 10,
    "version": "BM+71003",
    "wifi": "connected",
    "network": {
        "ip": IP,
        "gw": "10.10.30.1",
        "sn": "255.255.255.0",
        "dns": "10.10.30.1",
    },
}


@pytest.fixture
async def report(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "serial_number": SERIAL,
            CONF_IP_ADDRESS: IP,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: PASSWORD,
        },
        unique_id=SERIAL,
    )
    entry.add_to_hass(hass)

    with patch(f"{API}.get_states", return_value=STATES), patch(
        f"{API}.get_system_state", return_value=dict(SYSTEM_STATE)
    ), patch(f"{API}.get_configuration", return_value={}), patch(
        f"{API}.connect_and_monitor", new_callable=AsyncMock
    ), patch(f"{API}.stop", new_callable=AsyncMock):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield await async_get_config_entry_diagnostics(hass, entry)


@pytest.mark.parametrize(
    ("secret", "what"),
    [
        (PASSWORD, "the password"),
        (SERIAL, "the serial number"),
        (IP, "the lock's address"),
        ("10.10.30.1", "the gateway"),
        ("255.255.255.0", "the subnet mask"),
    ],
)
async def test_nothing_identifying_leaks(report, secret, what) -> None:
    assert secret not in json.dumps(report), f"{what} is still in the report"


async def test_nested_network_block_is_redacted(report) -> None:
    """The lock reports its own network settings inside getSystemState."""
    network = report["coordinators"]["system"]["data"]["network"]
    assert all(value == "**REDACTED**" for value in network.values())


@pytest.mark.parametrize(
    "path",
    [
        ("connection", "websocket_connected"),
        ("connection", "connection_count"),
        ("connection", "current_uptime_seconds"),
        ("connection", "websockets_version"),
        ("protocol", "client_counter"),
        ("protocol", "reassembly_buffer_bytes"),
        ("device", "sw_version"),
        ("config", "update_mode"),
    ],
)
async def test_useful_fields_survive(report, path) -> None:
    section, key = path
    assert key in report[section]


async def test_counters_are_kept(report) -> None:
    """Lifetime counters say a lot about a lock and identify nobody."""
    data = report["coordinators"]["system"]["data"]
    assert data["lock_cnt"] == 1458
    assert data["firmware"] == "1.6.2_2607171112"


async def test_last_request_omits_payload_values(report) -> None:
    """Commands hold no secrets, but the habit is the right one."""
    last = report["protocol"]["last_request"]
    if last is not None:
        assert "payload_keys" in last
        assert "payload" not in last


async def test_report_is_json_serialisable(report) -> None:
    """Home Assistant writes it to a file, so it has to survive that."""
    assert json.loads(json.dumps(report)) is not None
