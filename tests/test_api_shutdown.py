"""Shutting the client down, and what has to be true once stop() returns.

An entry can be unloaded at any moment, including in the two seconds before
the monitor has opened its first socket. The awkward case is that one: the
stop arrives while there is nothing to stop yet, and it still has to be
remembered when the loop finally runs. Everything here is about that gap and
about cleanup being finished rather than merely requested.
"""

import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.winkhaus_doorclient.api import DoorClient

API = "custom_components.winkhaus_doorclient.api"
SERIAL = "WH_021C6920F1B188"


@pytest.fixture
def client() -> DoorClient:
    return DoorClient(
        serial_number=SERIAL,
        ip="10.10.30.197",
        password="secret",
        session=MagicMock(),
        ssl_context=MagicMock(),
    )


class TestStopBeforeTheMonitorStarts:
    async def test_a_requested_stop_survives_the_start_delay(self, client) -> None:
        """The loop must not talk itself back into running.

        This is the gap that produced connection attempts during teardown:
        the entry was already gone, the stop had already been recorded, and
        the loop started anyway because entering it set the flag itself.
        """
        await client.stop()

        with patch(f"{API}.websockets.connect") as connect:
            await client._monitor_loop(start_delay=0)

        connect.assert_not_called()
        assert client._monitor_running is False

    async def test_no_socket_is_opened_when_the_entry_goes_away(self, client) -> None:
        """The same thing end to end, scheduled the way the setup does it."""
        with patch(f"{API}.websockets.connect") as connect:
            task = asyncio.create_task(client.connect_and_monitor(start_delay=30))
            await asyncio.sleep(0)  # let the task register itself

            await client.stop()

            with suppress(asyncio.CancelledError):
                await task

        connect.assert_not_called()
        assert task.done()


class TestStopEndsWhatIsRunning:
    async def test_a_running_monitor_is_cancelled(self, client) -> None:
        laeuft = asyncio.Event()

        async def endless(self, start_delay: float) -> None:
            laeuft.set()
            await asyncio.sleep(3600)

        with patch.object(DoorClient, "_monitor_loop", endless):
            task = asyncio.create_task(client.connect_and_monitor())
            await laeuft.wait()

            await client.stop()

        assert task.done()
        assert client._monitor_task is None
        assert client._monitor_running is False

    async def test_the_watchdog_is_gone_not_just_asked_to_go(self, client) -> None:
        """cancel() only requests it. stop() has to wait for the result."""
        client._watchdog_task = asyncio.create_task(client._watchdog_loop())
        await asyncio.sleep(0)
        task = client._watchdog_task

        await client.stop()

        assert task.done(), "stop() returned while the watchdog was still unwinding"
        assert client._watchdog_task is None

    async def test_the_watchdog_loop_honours_the_stop_flag(self, client) -> None:
        """Cancellation is not the only way out, or a missed cancel runs forever."""
        client._stop_requested = True

        await asyncio.wait_for(client._watchdog_loop(), timeout=1)

    async def test_an_open_socket_is_closed(self, client) -> None:
        client.ws_connected = True
        client._active_ws = AsyncMock()
        ws = client._active_ws

        await client.stop()

        ws.close.assert_awaited_once()
        assert client.ws_connected is False
        assert client._active_ws is None
        assert client.current_session_start is None

    async def test_stop_is_safe_to_call_twice(self, client) -> None:
        """Unload can race with a reload, and neither call may raise."""
        await client.stop()
        await client.stop()

        assert client._stop_requested is True
