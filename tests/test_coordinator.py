"""How the coordinator behaves when the lock stops answering.

Losing contact with a door lock is the normal case, not the exception: the
device is on WiFi and reconnects happen. What matters is that a short outage
stays invisible and a long one is reported once, without dropping the state
in between.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.winkhaus_doorclient.api import (
    WATCHDOG_SILENCE_THRESHOLD,
    DoorClient,
)
from custom_components.winkhaus_doorclient.const import (
    CONF_SCAN_INTERVAL,
    CONF_UPDATE_MODE,
    DOMAIN,
    MODE_POLLING,
)
from custom_components.winkhaus_doorclient.coordinator import (
    HYBRID_SCAN_INTERVAL,
    MAX_FAILURES_BEFORE_ALERT,
    STALE_STATE_AFTER,
    WinkhausCoordinator,
    WinkhausSystemCoordinator,
)

SERIAL = "WH_01TEST123456"

STATES = [
    {"name": "state", "value": "closed"},
    {"name": "locked", "value": False},
    {"name": "mode", "value": "day"},
]


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    item = MockConfigEntry(
        domain=DOMAIN,
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
def client() -> DoorClient:
    return DoorClient(serial_number=SERIAL, ip="192.168.1.50", password="secret", session=MagicMock(), ssl_context=MagicMock())


@pytest.fixture
def coordinator(hass, entry, client) -> WinkhausCoordinator:
    return WinkhausCoordinator(hass, entry, client)


def issue_exists(hass: HomeAssistant, coordinator) -> bool:
    return (
        ir.async_get(hass).async_get_issue(DOMAIN, coordinator.issue_id) is not None
    )


# ------------------------------------------------------------------ intervals

class TestUpdateInterval:
    def test_hybrid_polls_as_a_safety_net(self, coordinator):
        assert coordinator.update_interval.total_seconds() == HYBRID_SCAN_INTERVAL

    async def test_polling_mode_uses_the_configured_interval(self, hass, entry, client):
        hass.config_entries.async_update_entry(
            entry, options={CONF_UPDATE_MODE: MODE_POLLING, CONF_SCAN_INTERVAL: 45}
        )
        assert WinkhausCoordinator(hass, entry, client).update_interval.total_seconds() == 45

    def test_system_coordinator_runs_twice_a_day(self, hass, entry, client):
        system = WinkhausSystemCoordinator(hass, entry, client)
        assert system.update_interval.total_seconds() == 12 * 3600


# -------------------------------------------------------------------- outages

class TestFailureHandling:
    async def test_first_failure_without_data_raises(self, coordinator, client):
        """Nothing cached yet means Home Assistant should retry the setup."""
        with patch.object(client, "get_states", side_effect=OSError("no route")):
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()

    async def test_cached_data_survives_an_outage(self, coordinator, client):
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        with patch.object(client, "get_states", side_effect=OSError("no route")):
            await coordinator.async_refresh()

        assert coordinator.data == STATES
        assert coordinator.last_update_success is True

    async def test_failures_are_counted(self, coordinator, client):
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        with patch.object(client, "get_states", side_effect=OSError("no route")):
            await coordinator.async_refresh()
            await coordinator.async_refresh()

        assert coordinator.consecutive_failures == 2

    async def test_auth_failure_triggers_reauth(self, coordinator, client):
        """A 401 is not an outage - the password changed."""
        with patch.object(client, "get_states", side_effect=Exception("401 Unauthorized")):
            with pytest.raises(ConfigEntryAuthFailed):
                await coordinator._async_update_data()

    async def test_auth_failure_is_not_counted_as_an_outage(self, coordinator, client):
        with patch.object(client, "get_states", side_effect=Exception("401 Unauthorized")):
            with pytest.raises(ConfigEntryAuthFailed):
                await coordinator._async_update_data()

        assert coordinator.consecutive_failures == 0


# --------------------------------------------------------------- repair issue

class TestRepairIssue:
    async def test_stays_quiet_below_the_threshold(self, hass, coordinator, client):
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        with patch.object(client, "get_states", side_effect=OSError("no route")):
            for _ in range(MAX_FAILURES_BEFORE_ALERT - 1):
                await coordinator.async_refresh()

        assert not issue_exists(hass, coordinator)

    async def test_raised_after_three_failures(self, hass, coordinator, client):
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        with patch.object(client, "get_states", side_effect=OSError("no route")):
            for _ in range(MAX_FAILURES_BEFORE_ALERT):
                await coordinator.async_refresh()

        assert issue_exists(hass, coordinator)

    async def test_carries_the_serial_and_the_count(self, hass, coordinator, client):
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        with patch.object(client, "get_states", side_effect=OSError("no route")):
            for _ in range(MAX_FAILURES_BEFORE_ALERT):
                await coordinator.async_refresh()

        issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator.issue_id)
        assert issue.translation_placeholders["serial"] == SERIAL
        assert issue.translation_placeholders["failures"] == str(MAX_FAILURES_BEFORE_ALERT)

    async def test_cleared_when_the_lock_returns(self, hass, coordinator, client):
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        with patch.object(client, "get_states", side_effect=OSError("no route")):
            for _ in range(MAX_FAILURES_BEFORE_ALERT):
                await coordinator.async_refresh()
        assert issue_exists(hass, coordinator)

        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        assert not issue_exists(hass, coordinator)
        assert coordinator.consecutive_failures == 0


# ------------------------------------------------------------ system data

class TestSystemCoordinator:
    async def test_configuration_is_merged_in(self, hass, entry, client):
        system = WinkhausSystemCoordinator(hass, entry, client)

        with patch.object(client, "get_system_state", return_value={"firmware": "1.6.2"}), patch.object(
            client, "get_configuration", return_value={"system": {"name": "Front Door"}}
        ):
            data = await system._async_update_data()

        assert data["_config"]["system"]["name"] == "Front Door"

    async def test_missing_configuration_is_not_fatal(self, hass, entry, client):
        """The endpoint only supplies a display name - not worth failing over."""
        system = WinkhausSystemCoordinator(hass, entry, client)

        with patch.object(client, "get_system_state", return_value={"firmware": "1.6.2"}), patch.object(
            client, "get_configuration", side_effect=OSError("not supported")
        ):
            data = await system._async_update_data()

        assert data["firmware"] == "1.6.2"
        assert data["_config"] == {}

    async def test_system_state_failure_is_fatal(self, hass, entry, client):
        system = WinkhausSystemCoordinator(hass, entry, client)

        with patch.object(client, "get_system_state", side_effect=OSError("no route")):
            with pytest.raises(UpdateFailed):
                await system._async_update_data()


class TestOutageLogging:
    """A long outage should not fill the log with identical warnings."""

    async def test_warns_once_then_stays_quiet(self, hass, coordinator, client, caplog):
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        caplog.clear()
        with patch.object(client, "get_states", side_effect=OSError("no route")):
            for _ in range(10):
                await coordinator.async_refresh()

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

    async def test_warns_again_after_recovery(self, hass, coordinator, client, caplog):
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()
        with patch.object(client, "get_states", side_effect=OSError("no route")):
            await coordinator.async_refresh()
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        caplog.clear()
        with patch.object(client, "get_states", side_effect=OSError("gone again")):
            await coordinator.async_refresh()

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

    async def test_issue_is_raised_only_once(self, hass, coordinator, client):
        """Repeating async_create_issue would keep resetting the counter shown."""
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        with patch.object(client, "get_states", side_effect=OSError("no route")):
            for _ in range(10):
                await coordinator.async_refresh()

        issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator.issue_id)
        assert issue.translation_placeholders["failures"] == str(MAX_FAILURES_BEFORE_ALERT)


# ------------------------------------------------------------- stale state

class TestStaleState:
    """When the shown state is too old to keep claiming it.

    Keeping the last known state through an outage is right for a moment and
    wrong for an hour: the bolt can be thrown with a key, the keypad or the
    app while nothing is listening, so "locked" without a recent answer
    describes the past. Home Assistant's `entity-unavailable` rule says so
    too, and this is where the line is drawn.
    """

    async def test_a_recent_success_keeps_the_last_state(self, coordinator, client):
        """One failed fetch right after a good one changes nothing."""
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        with patch.object(client, "get_states", side_effect=OSError("no route")):
            await coordinator.async_refresh()

        assert coordinator.last_update_success is True
        assert coordinator.data == STATES

    async def test_entities_go_unavailable_when_the_state_goes_stale(
        self, coordinator, client
    ):
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        coordinator._last_good = time.time() - (STALE_STATE_AFTER + 10)

        with patch.object(client, "get_states", side_effect=OSError("no route")):
            await coordinator.async_refresh()

        assert coordinator.last_update_success is False

    async def test_the_data_itself_is_kept(self, coordinator, client):
        """Only the claim goes, not the content - diagnostics still show it."""
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        coordinator._last_good = time.time() - (STALE_STATE_AFTER + 10)

        with patch.object(client, "get_states", side_effect=OSError("no route")):
            await coordinator.async_refresh()

        assert coordinator.data == STATES

    async def test_a_push_keeps_the_state_fresh(self, coordinator):
        """In hybrid mode pushes are what keeps the age down, not polls."""
        coordinator._last_good = time.time() - (STALE_STATE_AFTER + 10)
        assert coordinator.state_is_stale() is True

        coordinator.async_set_updated_data(STATES)

        assert coordinator.state_is_stale() is False

    async def test_a_successful_fetch_keeps_the_state_fresh(self, coordinator, client):
        coordinator._last_good = time.time() - (STALE_STATE_AFTER + 10)

        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        assert coordinator.state_is_stale() is False

    async def test_a_push_brings_the_entities_back(self, coordinator, client):
        """Recovery needs no path of its own: the first message is enough."""
        with patch.object(client, "get_states", return_value=STATES):
            await coordinator.async_refresh()

        coordinator._last_good = time.time() - (STALE_STATE_AFTER + 10)
        with patch.object(client, "get_states", side_effect=OSError("no route")):
            await coordinator.async_refresh()
        assert coordinator.last_update_success is False

        coordinator.async_set_updated_data(STATES)

        assert coordinator.last_update_success is True

    def test_the_window_outlasts_a_nudge_cycle_plus_a_reconnect(self):
        """The reason this is not 300, and not 360 either.

        In a quiet household the watchdog's nudge is the only thing that
        refreshes the state, so the age sawtooths up to the nudge cycle -
        measured at 306 s over 249 nudges on a live lock. A reconnect adds
        to that, and the window has to cover both, or a dropped socket
        makes every entity flicker to unavailable and straight back.

        180 s of headroom over the threshold is the floor, not the target.
        """
        assert STALE_STATE_AFTER >= WATCHDOG_SILENCE_THRESHOLD + 180

    def test_the_window_closes_before_the_scheduled_fetch_gives_up(self):
        """Why it is not simply 600.

        Once the socket dies the nudges stop, and the scheduled fetch is
        the next thing that runs. With the window below that interval, the
        first failed fetch already lands on the far side of it. Set the two
        equal and that failure lands exactly on the boundary, pushing
        detection out by another full interval.
        """
        assert STALE_STATE_AFTER < HYBRID_SCAN_INTERVAL

    async def test_polling_mode_stays_available_without_a_socket(
        self, hass, entry, client
    ):
        """Freshness comes from the coordinator's own success, not the socket.

        Hanging this on the client's socket timestamp would have marked every
        polling installation unavailable from the first second, because in
        polling mode no socket is ever opened.
        """
        hass.config_entries.async_update_entry(
            entry, options={CONF_UPDATE_MODE: MODE_POLLING, CONF_SCAN_INTERVAL: 60}
        )
        polling = WinkhausCoordinator(hass, entry, client)

        with patch.object(client, "get_states", return_value=STATES):
            await polling.async_refresh()

        assert polling.last_update_success is True
        assert polling.state_is_stale() is False
