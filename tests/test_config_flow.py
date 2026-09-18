"""Tests for the Winkhaus Doorclient config flow."""

from ipaddress import ip_address
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.winkhaus_doorclient.config_flow import (
    parse_discovered_device,
)
from custom_components.winkhaus_doorclient.const import (
    CONF_SCAN_INTERVAL,
    CONF_UPDATE_MODE,
    DOMAIN,
    MODE_HYBRID,
    MODE_POLLING,
)

SERIAL = "WH_01TEST123456"
CONNECT = "custom_components.winkhaus_doorclient.api.DoorClient.connect"


def http_error(status: int) -> aiohttp.ClientResponseError:
    """The error aiohttp raises for a bad status, carrying its code."""
    return aiohttp.ClientResponseError(
        request_info=MagicMock(), history=(), status=status
    )


def entry_data(**overrides) -> dict:
    return {
        "serial_number": SERIAL,
        CONF_IP_ADDRESS: "192.168.1.50",
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "old_password",
        **overrides,
    }


@pytest.fixture(autouse=True)
def skip_integration_setup():
    """Creating an entry makes Home Assistant load the integration.

    These tests are about the flow, not about setup, and letting it run
    would have the coordinator reach for the network.
    """
    with patch(
        "custom_components.winkhaus_doorclient.async_setup_entry", return_value=True
    ):
        yield


@pytest.fixture
def configured_entry(hass: HomeAssistant) -> MockConfigEntry:
    """An entry that already exists, without starting the integration."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Winkhaus Door ({SERIAL})",
        data=entry_data(),
        unique_id=SERIAL,
    )
    entry.add_to_hass(hass)
    return entry


async def start_manual(hass: HomeAssistant) -> str:
    """Walk the menu up to the manual form and return the flow id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is data_entry_flow.FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["step_id"] == "manual"
    return result["flow_id"]


# --------------------------------------------------------------- manual setup

async def test_manual_flow_creates_entry(hass: HomeAssistant) -> None:
    flow_id = await start_manual(hass)

    with patch(CONNECT, new_callable=AsyncMock, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            flow_id, entry_data(**{CONF_PASSWORD: "secret"})
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Winkhaus Door ({SERIAL})"
    assert result["data"]["serial_number"] == SERIAL
    assert result["result"].unique_id == SERIAL


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ({"side_effect": http_error(401)}, "invalid_auth"),
        ({"side_effect": http_error(500)}, "cannot_connect"),
        ({"side_effect": OSError("network down")}, "unknown"),
        ({"return_value": False}, "cannot_connect"),
    ],
)
async def test_manual_flow_errors(hass: HomeAssistant, failure, expected) -> None:
    """Every failure mode maps to a translated error key."""
    flow_id = await start_manual(hass)

    with patch(CONNECT, new_callable=AsyncMock, **failure):
        result = await hass.config_entries.flow.async_configure(flow_id, entry_data())

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == expected


async def test_duplicate_serial_is_rejected(
    hass: HomeAssistant, configured_entry
) -> None:
    """The serial number is the unique id, so a lock cannot be added twice."""
    flow_id = await start_manual(hass)

    with patch(CONNECT, new_callable=AsyncMock, return_value=True):
        result = await hass.config_entries.flow.async_configure(flow_id, entry_data())

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ------------------------------------------------------------------- zeroconf

def discovery(host: str = "192.168.1.51", serial: str | None = SERIAL):
    """Build discovery info the way the zeroconf integration would.

    `host` is derived from `ip_address` and is not a constructor argument.
    """
    properties = {"serial": serial} if serial else {}
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        port=80,
        hostname=f"{serial or 'unknown'}.local.",
        type="_whdc-device._tcp.local.",
        name=f"{serial or 'unknown'}._whdc-device._tcp.local.",
        properties=properties,
    )


async def test_zeroconf_leads_to_auth(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovery(),
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "auth"


async def test_zeroconf_without_serial_aborts(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovery(serial=None),
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "no_serial_in_zeroconf"


async def test_zeroconf_updates_ip_of_known_lock(
    hass: HomeAssistant, configured_entry
) -> None:
    """A lock that moved to another address is followed automatically."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovery(host="192.168.1.77"),
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert configured_entry.data[CONF_IP_ADDRESS] == "192.168.1.77"


async def test_zeroconf_auth_creates_entry(hass: HomeAssistant) -> None:
    """The password form after discovery completes the setup."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovery(),
    )

    with patch(CONNECT, new_callable=AsyncMock, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "admin", CONF_PASSWORD: "secret"},
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_IP_ADDRESS] == "192.168.1.51"


# ---------------------------------------------------------------- reconfigure

async def test_reconfigure_changes_ip(hass: HomeAssistant, configured_entry) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": configured_entry.entry_id,
        },
    )
    assert result["step_id"] == "reconfigure"

    with patch(CONNECT, new_callable=AsyncMock, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_IP_ADDRESS: "192.168.99.10", CONF_USERNAME: "admin"},
        )
        await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert configured_entry.data[CONF_IP_ADDRESS] == "192.168.99.10"


async def test_reconfigure_keeps_password_when_left_empty(
    hass: HomeAssistant, configured_entry
) -> None:
    """An empty password field must not wipe the stored secret."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": configured_entry.entry_id,
        },
    )

    with patch(CONNECT, new_callable=AsyncMock, return_value=True):
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_IP_ADDRESS: "192.168.99.10", CONF_USERNAME: "admin"},
        )
        await hass.async_block_till_done()

    assert configured_entry.data[CONF_PASSWORD] == "old_password"


async def test_reconfigure_stores_new_password(
    hass: HomeAssistant, configured_entry
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": configured_entry.entry_id,
        },
    )

    with patch(CONNECT, new_callable=AsyncMock, return_value=True):
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_IP_ADDRESS: "192.168.1.50",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "brand_new",
            },
        )
        await hass.async_block_till_done()

    assert configured_entry.data[CONF_PASSWORD] == "brand_new"


async def test_reconfigure_keeps_old_address_on_failure(
    hass: HomeAssistant, configured_entry
) -> None:
    """A typo must not replace a working address with an unreachable one."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": configured_entry.entry_id,
        },
    )

    with patch(CONNECT, new_callable=AsyncMock, return_value=False):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_IP_ADDRESS: "192.168.99.99", CONF_USERNAME: "admin"},
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"
    assert configured_entry.data[CONF_IP_ADDRESS] == "192.168.1.50"


# --------------------------------------------------------------------- reauth

async def test_reauth_updates_password(hass: HomeAssistant, configured_entry) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": configured_entry.entry_id,
        },
        data=configured_entry.data,
    )
    assert result["step_id"] == "reauth_confirm"

    with patch(CONNECT, new_callable=AsyncMock, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "recovered"}
        )
        await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert configured_entry.data[CONF_PASSWORD] == "recovered"


async def test_reauth_rejects_wrong_password(
    hass: HomeAssistant, configured_entry
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": configured_entry.entry_id,
        },
        data=configured_entry.data,
    )

    with patch(CONNECT, new_callable=AsyncMock, side_effect=http_error(401)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "still_wrong"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_auth"
    assert configured_entry.data[CONF_PASSWORD] == "old_password"


# -------------------------------------------------------------- options flow

async def test_options_hybrid_finishes_immediately(
    hass: HomeAssistant, configured_entry
) -> None:
    result = await hass.config_entries.options.async_init(configured_entry.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_MODE: MODE_HYBRID}
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UPDATE_MODE] == MODE_HYBRID


async def test_options_polling_asks_for_interval(
    hass: HomeAssistant, configured_entry
) -> None:
    result = await hass.config_entries.options.async_init(configured_entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_MODE: MODE_POLLING}
    )
    assert result["step_id"] == "polling"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 90}
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SCAN_INTERVAL] == 90


# ------------------------------------------------------- discovery parsing

class FakeServiceInfo:
    """Minimal stand-in for a zeroconf service record."""

    def __init__(self, addresses=None, properties=None):
        self.addresses = addresses
        self.properties = properties


def packed(ip: str) -> bytes:
    from socket import inet_aton

    return inet_aton(ip)


class TestParseDiscoveredDevice:
    """The only part of the network scan that carries logic."""

    def test_serial_from_property(self):
        info = FakeServiceInfo([packed("10.0.0.5")], {b"serial": b"WH_ABC"})
        assert parse_discovered_device("ignored.local.", info) == ("WH_ABC", "10.0.0.5")

    @pytest.mark.parametrize("key", [b"serial", b"sn", b"id", b"mac"])
    def test_every_known_property_key(self, key):
        info = FakeServiceInfo([packed("10.0.0.5")], {key: b"WH_ABC"})
        assert parse_discovered_device("x.local.", info)[0] == "WH_ABC"

    def test_first_matching_key_wins(self):
        info = FakeServiceInfo(
            [packed("10.0.0.5")], {b"mac": b"WRONG", b"serial": b"WH_RIGHT"}
        )
        assert parse_discovered_device("x.local.", info)[0] == "WH_RIGHT"

    def test_service_name_is_the_fallback(self):
        """Firmware that publishes no serial still yields a usable name."""
        info = FakeServiceInfo([packed("10.0.0.5")], {})
        result = parse_discovered_device("WH_FROMNAME._whdc-device._tcp.local.", info)
        assert result == ("WH_FROMNAME", "10.0.0.5")

    def test_undecodable_property_falls_back(self):
        info = FakeServiceInfo([packed("10.0.0.5")], {b"serial": b"\xff\xfe"})
        assert parse_discovered_device("WH_NAME.local.", info)[0] == "WH_NAME"

    @pytest.mark.parametrize(
        "info",
        [
            None,
            FakeServiceInfo(addresses=None),
            FakeServiceInfo(addresses=[]),
            FakeServiceInfo(addresses=[b"too-short"]),
        ],
    )
    def test_unusable_records_are_skipped(self, info):
        assert parse_discovered_device("x.local.", info) is None


# ------------------------------------------------------------- network scan

def browser_yielding(*records):
    """A ServiceBrowser replacement that feeds records to the handler."""
    from types import SimpleNamespace

    def factory(zc, service_type, handlers):
        for name, info in records:
            zc.get_service_info = lambda *_args, _i=info: _i
            handlers[0](zc, service_type, name, SimpleNamespace(name="Added"))
        return MagicMock()

    return factory


async def run_scan(hass: HomeAssistant, browser_factory):
    """Start the menu and walk into the scan step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    module = "custom_components.winkhaus_doorclient.config_flow"

    with patch(f"{module}.async_get_instance", return_value=MagicMock()), patch(
        f"{module}.ServiceBrowser", side_effect=browser_factory
    ), patch(f"{module}.DISCOVERY_TIMEOUT", 0):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "scan"}
        )


async def test_scan_without_devices_aborts(hass: HomeAssistant) -> None:
    result = await run_scan(hass, browser_yielding())

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_scan_offers_found_device(hass: HomeAssistant) -> None:
    result = await run_scan(
        hass,
        browser_yielding(
            ("WH_A._whdc-device._tcp.local.",
             FakeServiceInfo([packed("10.0.0.5")], {b"serial": SERIAL.encode()})),
        ),
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "pick"


async def test_pick_leads_to_auth_and_creates_entry(hass: HomeAssistant) -> None:
    result = await run_scan(
        hass,
        browser_yielding(
            ("WH_A._whdc-device._tcp.local.",
             FakeServiceInfo([packed("10.0.0.5")], {b"serial": SERIAL.encode()})),
        ),
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": SERIAL}
    )
    assert result["step_id"] == "auth"

    with patch(CONNECT, new_callable=AsyncMock, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "admin", CONF_PASSWORD: "secret"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_IP_ADDRESS] == "10.0.0.5"


# ------------------------------------------------- remaining failure paths

@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ({"side_effect": http_error(500)}, "cannot_connect"),
        ({"side_effect": OSError("boom")}, "unknown"),
        ({"return_value": False}, "cannot_connect"),
    ],
)
async def test_reauth_failure_paths(
    hass: HomeAssistant, configured_entry, failure, expected
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": configured_entry.entry_id,
        },
        data=configured_entry.data,
    )

    with patch(CONNECT, new_callable=AsyncMock, **failure):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "whatever"}
        )

    assert result["errors"]["base"] == expected


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ({"side_effect": http_error(401)}, "invalid_auth"),
        ({"side_effect": http_error(500)}, "cannot_connect"),
        ({"side_effect": OSError("boom")}, "unknown"),
    ],
)
async def test_reconfigure_failure_paths(
    hass: HomeAssistant, configured_entry, failure, expected
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": configured_entry.entry_id,
        },
    )

    with patch(CONNECT, new_callable=AsyncMock, **failure):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_IP_ADDRESS: "192.168.99.99", CONF_USERNAME: "admin"},
        )

    assert result["errors"]["base"] == expected


async def test_scan_ignores_non_added_events(hass: HomeAssistant) -> None:
    """Zeroconf also reports Removed and Updated; only Added counts."""
    from types import SimpleNamespace

    def factory(zc, service_type, handlers):
        zc.get_service_info = lambda *_: FakeServiceInfo(
            [packed("10.0.0.5")], {b"serial": b"WH_GONE"}
        )
        handlers[0](zc, service_type, "WH_GONE.local.", SimpleNamespace(name="Removed"))
        return MagicMock()

    result = await run_scan(hass, factory)

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"
