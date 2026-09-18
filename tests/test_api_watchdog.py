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

from custom_components.winkhaus_doorclient.api import DoorClient

SERIAL = "WH_021C6920F1B188"
STATES = [{"name": "state", "value": "closed"}]


@pytest.fixture
def client() -> DoorClient:
    item = DoorClient(serial_number=SERIAL, ip="10.10.30.197", password="secret", session=MagicMock(), ssl_context=MagicMock())
    item.ws_connected = True
    item._active_ws = AsyncMock()
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

        with patch.object(client, "async_send_payload", new_callable=AsyncMock) as send:
            await run_watchdog(client, rounds=2)

        send.assert_not_awaited()

    async def test_silence_triggers_a_status_request(self, client):
        """After 75 seconds the watchdog asks rather than assuming."""
        client.last_message_time = time.time() - 80

        with patch.object(client, "async_send_payload", new_callable=AsyncMock) as send:
            await run_watchdog(client)

        send.assert_awaited()
        assert send.await_args.args[0] == "/api/v1/getStates"

    async def test_no_ping_without_a_connection(self, client):
        client.ws_connected = False
        client.last_message_time = time.time() - 300

        with patch.object(client, "async_send_payload", new_callable=AsyncMock) as send:
            await run_watchdog(client)

        send.assert_not_awaited()

    async def test_a_failed_ping_does_not_stop_the_loop(self, client):
        client.last_message_time = time.time() - 80

        with patch.object(
            client, "async_send_payload", side_effect=OSError("socket gone")
        ), patch.object(client, "get_states", return_value=STATES):
            await run_watchdog(client)  # must not raise


class TestHttpFallback:
    async def test_used_when_the_ping_stays_unanswered(self, client):
        """The lock is unresponsive, so the state is pulled over HTTP."""
        client.last_message_time = time.time() - 90

        received = []
        client.on_state_change = received.append

        with patch.object(client, "async_send_payload", new_callable=AsyncMock), patch.object(
            client, "get_states", return_value=STATES
        ) as get_states:
            await run_watchdog(client)

        get_states.assert_called_once()
        assert received == [STATES]

    async def test_the_timestamp_is_refreshed_afterwards(self, client):
        """Otherwise the fallback would fire again on the very next round."""
        client.last_message_time = time.time() - 90

        with patch.object(client, "async_send_payload", new_callable=AsyncMock), patch.object(
            client, "get_states", return_value=STATES
        ):
            await run_watchdog(client)

        assert time.time() - client.last_message_time < 5

    async def test_a_failing_fallback_does_not_stop_the_loop(self, client):
        client.last_message_time = time.time() - 90

        with patch.object(client, "async_send_payload", new_callable=AsyncMock), patch.object(
            client, "get_states", side_effect=OSError("no route")
        ):
            await run_watchdog(client)  # must not raise

    async def test_no_callback_means_no_crash(self, client):
        client.last_message_time = time.time() - 90
        client.on_state_change = None

        with patch.object(client, "async_send_payload", new_callable=AsyncMock), patch.object(
            client, "get_states", return_value=STATES
        ):
            await run_watchdog(client)

    async def test_an_answered_ping_skips_the_fallback(self, client):
        """A reply during the grace period means the link is alive."""
        client.last_message_time = time.time() - 80

        async def answer(*_args, **_kwargs):
            client.last_message_time = time.time()
            return True

        with patch.object(client, "async_send_payload", side_effect=answer), patch.object(
            client, "get_states", return_value=STATES
        ) as get_states:
            await run_watchdog(client)

        get_states.assert_not_called()
