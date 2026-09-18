"""Entity actions must tell the user when they did not get through.

The API client reports a refusal by returning False rather than raising, so
ignoring the return value left the user pressing a button with no feedback
while the entity kept showing the old state.
"""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.winkhaus_doorclient.const import DOMAIN

SERIAL = "WH_01TEST123456"
API = "custom_components.winkhaus_doorclient.api.DoorClient"
PREFIX = f"winkhaus_door_{SERIAL.lower()}"

STATES = [
    {"name": "state", "value": "closed"},
    {"name": "locked", "value": False},
    {"name": "mode", "value": "day"},
]


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
        f"{API}.get_system_state", return_value={"firmware": "1.6.2_2607171112"}
    ), patch(f"{API}.get_configuration", return_value={}), patch(
        f"{API}.connect_and_monitor", new_callable=AsyncMock
    ), patch(f"{API}.stop", new_callable=AsyncMock):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


LOCK_ACTIONS = [
    ("lock", "lock", {}),
    ("lock", "unlock", {}),
    ("lock", "open", {}),
    (DOMAIN, "set_day_mode", {}),
    (DOMAIN, "set_night_mode", {}),
]


@pytest.mark.parametrize(("domain", "service", "data"), LOCK_ACTIONS)
async def test_lock_action_reports_a_refusal(
    hass: HomeAssistant, loaded, domain, service, data
) -> None:
    with patch(f"{API}.async_execute_command", return_value=False):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                domain,
                service,
                {"entity_id": f"lock.{PREFIX}_lock", **data},
                blocking=True,
            )


@pytest.mark.parametrize(("domain", "service", "data"), LOCK_ACTIONS)
async def test_lock_action_stays_quiet_when_it_works(
    hass: HomeAssistant, loaded, domain, service, data
) -> None:
    with patch(f"{API}.async_execute_command", return_value=True):
        await hass.services.async_call(
            domain,
            service,
            {"entity_id": f"lock.{PREFIX}_lock", **data},
            blocking=True,
        )


async def test_mode_select_reports_a_refusal(hass: HomeAssistant, loaded) -> None:
    with patch(f"{API}.async_execute_command", return_value=False):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": f"select.{PREFIX}_mode", "option": "night"},
                blocking=True,
            )


async def test_clear_errors_reports_a_refusal(hass: HomeAssistant, loaded) -> None:
    with patch(f"{API}.async_unblock", return_value=False):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "button",
                "press",
                {"entity_id": f"button.{PREFIX}_clear_errors"},
                blocking=True,
            )


async def test_clear_errors_reports_an_exception(hass: HomeAssistant, loaded) -> None:
    with patch(f"{API}.async_unblock", side_effect=OSError("no route")):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "button",
                "press",
                {"entity_id": f"button.{PREFIX}_clear_errors"},
                blocking=True,
            )


async def test_system_state_dump_reports_a_failure(hass: HomeAssistant, loaded) -> None:
    with patch(f"{API}.get_system_state", side_effect=OSError("no route")):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                "get_system_state",
                {"entity_id": f"lock.{PREFIX}_lock"},
                blocking=True,
            )


# ------------------------------------------------- translated error messages

async def test_error_message_is_translated(hass: HomeAssistant, loaded) -> None:
    """The user should read a sentence, not a translation key."""
    with patch(f"{API}.async_execute_command", return_value=False):
        with pytest.raises(HomeAssistantError) as raised:
            await hass.services.async_call(
                "lock", "lock", {"entity_id": f"lock.{PREFIX}_lock"}, blocking=True
            )

    assert "Could not reach the lock" in str(raised.value)


async def test_error_message_names_the_command(hass: HomeAssistant, loaded) -> None:
    with patch(f"{API}.async_execute_command", return_value=False):
        with pytest.raises(HomeAssistantError) as raised:
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": f"select.{PREFIX}_mode", "option": "night"},
                blocking=True,
            )

    assert "night" in str(raised.value)


@pytest.mark.parametrize(
    "key",
    [
        "command_failed",
        "mode_failed",
        "unblock_failed",
        "unblock_unreachable",
        "system_state_failed",
    ],
)
async def test_every_key_is_translated_in_both_languages(key) -> None:
    """A missing entry shows the raw key to the user."""
    import json
    import pathlib

    base = pathlib.Path(__file__).parent.parent / "custom_components" / "winkhaus_doorclient"
    for datei in [base / "strings.json", base / "translations" / "de.json"]:
        data = json.loads(datei.read_text(encoding="utf-8"))
        assert key in data.get("exceptions", {}), f"{key} missing in {datei.name}"
        assert data["exceptions"][key]["message"].strip()


async def test_placeholders_match_between_languages() -> None:
    """A placeholder that exists in one language only renders as literal text."""
    import json
    import pathlib
    import re

    base = pathlib.Path(__file__).parent.parent / "custom_components" / "winkhaus_doorclient"
    en = json.loads((base / "strings.json").read_text(encoding="utf-8"))["exceptions"]
    de = json.loads((base / "translations" / "de.json").read_text(encoding="utf-8"))["exceptions"]

    for key in en:
        placeholders_en = set(re.findall(r"\{(\w+)\}", en[key]["message"]))
        placeholders_de = set(re.findall(r"\{(\w+)\}", de[key]["message"]))
        assert placeholders_en == placeholders_de, f"{key}: {placeholders_en} vs {placeholders_de}"


async def test_no_hardcoded_messages_left() -> None:
    import pathlib
    import re

    base = pathlib.Path(__file__).parent.parent / "custom_components" / "winkhaus_doorclient"
    offenders = []
    for f in base.glob("*.py"):
        for m in re.findall(r'HomeAssistantError\(\s*f?"', f.read_text(encoding="utf-8")):
            offenders.append(f.name)
    assert not offenders, f"hard-coded error text in: {set(offenders)}"
