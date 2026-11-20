"""The watchdog that notices when the lock has gone quiet.

The lock does not push on a timer, it only answers. So a long silence is
ambiguous: either nothing happened, or the connection is dead. The watchdog
resolves that by asking, and falls back to HTTP when even that stays
unanswered.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.winkhaus_doorclient.api import (
    WATCHDOG_FALLBACK_THRESHOLD,
    WATCHDOG_SILENCE_THRESHOLD,
    DoorClient,
)

SERIAL = "WH_021C6920F1B188"
STATES = [{"name": "state", "value": "closed"}]


@pytest.fixture
def client() -> DoorClient:
    item = DoorClient(serial_number=SERIAL, ip="10.10.30.197", password="secret", session=MagicMock(), ssl_context=MagicMock())
    item.ws_connected = True
    item._active_ws = AsyncMock()
    item.shared_key = b"k" * 32
    item.client_challenge = bytes(range(32))

    # As above: the lock answers, so the nudge the watchdog sends gets a reply.
    async def reply(_frame):
        waiting = item._ws_reply
        if waiting is not None and not waiting.done():
            waiting.set_result('{"XC_SUC": {}}')

    item._active_ws.send.side_effect = reply
    return item


async def run_watchdog(client, *, rounds: int = 1):
    """Let the loop run a fixed number of iterations, without waiting."""
    schlaefe = 0

    async def instant_sleep(_seconds):
        nonlocal schlaefe
        schlaefe += 1
        # The loop sleeps 5s per round, plus 5s after a ping
        if schlaefe > rounds * 3:
            raise asyncio.CancelledError

    with patch("asyncio.sleep", side_effect=instant_sleep):
        try:
            await client._watchdog_loop()
        except asyncio.CancelledError:
            pass


class TestQuietConnection:
    async def test_recent_traffic_means_no_ping(self, client):
        client.last_message_time = time.time()

        with patch.object(client, "_send_status_nudge", new_callable=AsyncMock) as send:
            await run_watchdog(client, rounds=2)

        send.assert_not_awaited()

    async def test_silence_triggers_a_status_request(self, client):
        """After the silence threshold the watchdog asks rather than assuming."""
        client.last_message_time = time.time() - (WATCHDOG_SILENCE_THRESHOLD + 5)

        await run_watchdog(client)

        # Checked on the frame, not on a patched-out call: the nudge must not
        # wait for an answer, and a mock in its place would hide that.
        # The loop gets through more than one round in this harness, so the
        # count is not the point - what went out is.
        client._active_ws.send.assert_awaited()
        frame = client._active_ws.send.await_args_list[0].args[0]
        endpoint = client._decrypt_own_frame(frame).split("\n", 1)[0]
        assert endpoint == "/api/v1/getStates"

    async def test_no_ping_without_a_connection(self, client):
        client.ws_connected = False
        client.last_message_time = time.time() - (WATCHDOG_SILENCE_THRESHOLD + 5)

        with patch.object(client, "_send_status_nudge", new_callable=AsyncMock) as send:
            await run_watchdog(client)

        send.assert_not_awaited()

    async def test_a_broken_socket_does_not_stop_the_loop(self, client):
        """The guarantee moved rather than disappeared.

        The watchdog used to wrap its nudge in a handler. The nudge now
        swallows its own transport errors and raises nothing, so the handler
        became unreachable and was removed - which means this has to be
        proven at the socket, not by making the nudge raise something it
        cannot raise.
        """
        client.last_message_time = time.time() - (WATCHDOG_SILENCE_THRESHOLD + 5)
        client._active_ws.send.side_effect = OSError("socket gone")

        with patch.object(client, "get_states_http", return_value=STATES):
            await run_watchdog(client)  # must not raise


class TestHttpFallback:
    async def test_used_when_the_ping_stays_unanswered(self, client):
        """The lock is unresponsive, so the state is pulled over HTTP."""
        client.last_message_time = time.time() - (WATCHDOG_FALLBACK_THRESHOLD + 5)

        received = []
        client.on_state_change = received.append

        with patch.object(client, "_send_status_nudge", new_callable=AsyncMock), patch.object(
            client, "get_states_http", return_value=STATES
        ) as get_states:
            await run_watchdog(client)

        get_states.assert_called_once()
        assert received == [STATES]

    async def test_the_timestamp_is_refreshed_afterwards(self, client):
        """Otherwise the fallback would fire again on the very next round."""
        client.last_message_time = time.time() - (WATCHDOG_FALLBACK_THRESHOLD + 5)

        with patch.object(client, "_send_status_nudge", new_callable=AsyncMock), patch.object(
            client, "get_states_http", return_value=STATES
        ):
            await run_watchdog(client)

        assert time.time() - client.last_message_time < 5

    async def test_a_failing_fallback_does_not_stop_the_loop(self, client):
        client.last_message_time = time.time() - (WATCHDOG_FALLBACK_THRESHOLD + 5)

        with patch.object(client, "_send_status_nudge", new_callable=AsyncMock), patch.object(
            client, "get_states_http", side_effect=OSError("no route")
        ):
            await run_watchdog(client)  # must not raise

    async def test_no_callback_means_no_crash(self, client):
        client.last_message_time = time.time() - (WATCHDOG_FALLBACK_THRESHOLD + 5)
        client.on_state_change = None

        with patch.object(client, "_send_status_nudge", new_callable=AsyncMock), patch.object(
            client, "get_states_http", return_value=STATES
        ):
            await run_watchdog(client)

    async def test_an_answered_ping_skips_the_fallback(self, client):
        """A reply during the grace period means the link is alive."""
        client.last_message_time = time.time() - (WATCHDOG_SILENCE_THRESHOLD + 5)

        async def answer(*_args, **_kwargs):
            client.last_message_time = time.time()
            return True

        with patch.object(client, "async_send_payload", side_effect=answer), patch.object(
            client, "get_states_http", return_value=STATES
        ) as get_states:
            await run_watchdog(client)

        get_states.assert_not_called()


class TestLostContact:
    """What the watchdog does when neither route answers any more.

    The nudge going unanswered is already evidence: it is a direct request on
    an open socket. If the HTTP fallback then fails as well, nothing at all is
    reaching the lock. The client reports that and decides nothing - whether
    the shown state has to go is the coordinator's rule.
    """

    async def test_one_failed_fallback_does_not_report(self, client):
        """A single HTTP hiccup must not grey out the dashboard."""
        client.last_message_time = time.time() - (WATCHDOG_FALLBACK_THRESHOLD + 5)
        reports = []
        client.on_contact_lost = lambda: reports.append(True)

        with patch.object(
            client, "_send_status_nudge", new_callable=AsyncMock
        ), patch.object(client, "get_states_http", side_effect=OSError("no route")):
            await run_watchdog(client, rounds=1)

        assert reports == []

    async def test_a_second_failed_fallback_reports_lost_contact(self, client):
        client.last_message_time = time.time() - (WATCHDOG_FALLBACK_THRESHOLD + 5)
        reports = []
        client.on_contact_lost = lambda: reports.append(True)

        with patch.object(
            client, "_send_status_nudge", new_callable=AsyncMock
        ), patch.object(client, "get_states_http", side_effect=OSError("no route")):
            await run_watchdog(client, rounds=2)

        assert reports == [True]

    async def test_the_loss_is_reported_once_per_outage(self, client):
        """An hour of silence must not call the coordinator every ten seconds."""
        client.last_message_time = time.time() - (WATCHDOG_FALLBACK_THRESHOLD + 5)
        reports = []
        client.on_contact_lost = lambda: reports.append(True)

        with patch.object(
            client, "_send_status_nudge", new_callable=AsyncMock
        ), patch.object(client, "get_states_http", side_effect=OSError("no route")):
            await run_watchdog(client, rounds=5)

        assert reports == [True]

    async def test_a_working_fallback_clears_the_count(self, client):
        """Otherwise two failures an hour apart would count as an outage."""
        client.last_message_time = time.time() - (WATCHDOG_FALLBACK_THRESHOLD + 5)
        client._fallback_failures = 1

        with patch.object(
            client, "_send_status_nudge", new_callable=AsyncMock
        ), patch.object(client, "get_states_http", return_value=STATES):
            await run_watchdog(client, rounds=1)

        assert client._fallback_failures == 0

    async def test_no_contact_callback_means_no_crash(self, client):
        client.last_message_time = time.time() - (WATCHDOG_FALLBACK_THRESHOLD + 5)
        client.on_contact_lost = None

        with patch.object(
            client, "_send_status_nudge", new_callable=AsyncMock
        ), patch.object(client, "get_states_http", side_effect=OSError("no route")):
            await run_watchdog(client, rounds=2)  # must not raise

