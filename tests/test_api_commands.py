"""Sending commands: WebSocket first, HTTP as the fallback.

This is the hybrid dispatch. Which path a command takes decides how fast it
arrives and whether a state update follows on its own, so both are pinned.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from custom_components.winkhaus_doorclient.api import DeviceRefusedError, DoorClient

API = "custom_components.winkhaus_doorclient.api"
SERIAL = "WH_021C6920F1B188"
STATES = [{"name": "state", "value": "closed"}, {"name": "mode", "value": "day"}]


@pytest.fixture
def client() -> DoorClient:
    return DoorClient(serial_number=SERIAL, ip="10.10.30.197", password="secret", session=MagicMock(), ssl_context=MagicMock())


@pytest.fixture
def connected(client) -> DoorClient:
    """A client with a live WebSocket that records what was sent."""
    client.ws_connected = True
    client._active_ws = AsyncMock()
    client.shared_key = b"k" * 32
    client.client_challenge = bytes(range(32))
    client.device_challenge = bytes(range(32, 64))

    # The lock answers every request it receives, so a socket that stays
    # silent is not a simplification - it is a device that does not exist, and
    # any caller that waits for a reply would hang against it. This one
    # answers with the empty acknowledgement a command gets; a test that needs
    # different content sets `ws_answer` before acting.
    client.ws_answer = '{"XC_SUC": {}}'

    async def reply(_frame):
        waiting = client._ws_reply
        if waiting is not None and not waiting.done():
            waiting.set_result(client.ws_answer)

    client._active_ws.send.side_effect = reply
    return client


def sent_frame(client) -> bytes:
    return client._active_ws.send.call_args.args[0]


def decrypt_frame(client, frame: bytes) -> str:
    """Undo what async_send_payload did, using the client's own IV rules."""
    counter = int.from_bytes(frame[4:8], "big")
    iv = client._get_iv(client.client_challenge, counter)
    return AESCCM(client.shared_key, tag_length=16).decrypt(iv, frame[8:], None).decode()


# ------------------------------------------------------------ dispatch path

class TestSendPayload:
    async def test_uses_the_websocket_when_connected(self, connected):
        assert await connected.async_send_payload("/api/v1/control", {"command": "night"})
        connected._active_ws.send.assert_awaited_once()

    async def test_frame_carries_endpoint_and_payload(self, connected):
        await connected.async_send_payload("/api/v1/control", {"command": "night"})

        endpoint, payload = decrypt_frame(connected, sent_frame(connected)).split("\n", 1)
        assert endpoint == "/api/v1/control"
        assert json.loads(payload) == {"command": "night"}

    async def test_header_marks_a_final_client_frame(self, connected):
        """0x85 is the FIN bit plus packet type 5, what the client sends."""
        await connected.async_send_payload("/api/v1/control", {"command": "day"})

        frame = sent_frame(connected)
        assert frame[0] == 0x85
        assert frame[1] == 0x00

    async def test_declared_length_matches_the_ciphertext(self, connected):
        await connected.async_send_payload("/api/v1/control", {"command": "day"})

        frame = sent_frame(connected)
        assert int.from_bytes(frame[2:4], "big") == len(frame) - 8

    async def test_counter_advances_with_every_frame(self, connected):
        counters = []
        for _ in range(3):
            await connected.async_send_payload("/api/v1/getStates", {})
            counters.append(int.from_bytes(sent_frame(connected)[4:8], "big"))

        assert counters == sorted(set(counters))
        assert len(counters) == 3

    async def test_empty_payload_becomes_an_empty_object(self, connected):
        await connected.async_send_payload("/api/v1/getStates", {})

        _, payload = decrypt_frame(connected, sent_frame(connected)).split("\n", 1)
        assert payload == "{}"

    async def test_the_request_is_remembered(self, connected):
        await connected.async_send_payload("/api/v1/control", {"command": "night"})

        endpoint, payload, _ = connected._last_request
        assert endpoint == "/api/v1/control"
        assert payload == {"command": "night"}


class TestWsRequest:
    """Request and response over the WebSocket.

    The lock carries no request identifier, so the answer is identified by
    being the next reply-typed message to arrive. That only holds while one
    request is in flight, which is what the lock here enforces.
    """

    async def test_the_answer_reaches_the_caller(self, connected):
        connected.ws_answer = '{"XC_SUC": {"firmware": "1.6.2", "battery": 0}}'

        answer = await connected.async_ws_request("/api/v1/getSystemState")

        assert answer == {"firmware": "1.6.2", "battery": 0}

    async def test_an_empty_acknowledgement_is_an_empty_answer(self, connected):
        """What a command gets back. Not an error, just nothing to report."""
        assert await connected.async_ws_request("/api/v1/control", {"command": "day"}) == {}

    async def test_a_refusal_is_raised_as_its_own_kind(self, connected):
        """Distinguishing a refusal from a transport failure is the point: a
        refusal must not be retried elsewhere, since the lock did receive it."""
        connected.ws_answer = '{"XC_ERR": {"code": "E_BUSY"}}'

        with pytest.raises(DeviceRefusedError, match="E_BUSY"):
            await connected.async_ws_request("/api/v1/control", {"command": "day"})

    async def test_a_refusal_without_a_text_still_names_its_code(self, connected):
        connected.ws_answer = '{"XC_ERR": {"code": 0}}'

        with pytest.raises(DeviceRefusedError, match="0"):
            await connected.async_ws_request("/api/v1/getParams")

    async def test_a_missing_answer_drops_the_socket(self, connected):
        """The order is the only correlation, so a late answer would be read as
        the next one. Dropping the socket is the only way back in step."""
        connected._active_ws.send.side_effect = None  # the lock goes quiet

        with pytest.raises(TimeoutError):
            await connected.async_ws_request("/api/v1/getParams", timeout=0.01)

        connected._active_ws.close.assert_awaited_once()

    async def test_a_closed_socket_is_not_attempted(self, connected):
        connected.ws_connected = False

        with pytest.raises(RuntimeError, match="not connected"):
            await connected.async_ws_request("/api/v1/getParams")

    async def test_requests_are_served_one_at_a_time(self, connected):
        """Two callers must not have their answers swapped, so the second
        request does not go out before the first one is answered."""
        ablauf = []

        async def slow_reply(frame):
            endpoint = decrypt_frame(connected, frame).split("\n", 1)[0]
            ablauf.append(f"sent {endpoint}")
            waiting = connected._ws_reply

            async def answer():
                await asyncio.sleep(0.01)
                ablauf.append(f"answered {endpoint}")
                if waiting is not None and not waiting.done():
                    waiting.set_result('{"XC_SUC": {}}')

            asyncio.create_task(answer())

        connected._active_ws.send.side_effect = slow_reply

        await asyncio.gather(
            connected.async_ws_request("/api/v1/getParams"),
            connected.async_ws_request("/api/v1/getGroups"),
        )

        assert ablauf == [
            "sent /api/v1/getParams",
            "answered /api/v1/getParams",
            "sent /api/v1/getGroups",
            "answered /api/v1/getGroups",
        ]


class TestExplicitHttpRead:
    """`get_states_http` exists so the two callers whose purpose is the other
    transport cannot accidentally be routed back onto the socket."""

    async def test_it_reads_over_http_even_with_a_live_socket(self, connected):
        connected.ws_answer = '{"XC_SUC": {"state": "open"}}'

        with patch.object(
            connected, "_request", return_value={"state": "closed", "mode": "day"}
        ) as http:
            assert await connected.get_states_http() == [
                {"name": "state", "value": "closed"},
                {"name": "mode", "value": "day"},
            ]

        assert http.call_args.args[0] == "/api/v1/getStates"
        connected._active_ws.send.assert_not_called()


class TestStatusNudge:
    """The status nudge must not wait for an answer.

    Measured in production on 15.11.2026: the lock does not answer
    `/api/v1/getStates` with a reply-typed frame the way it answers
    `getGroups` or `getSystemState`. Sent through the correlation layer it
    timed out every time, which dropped the socket and rebuilt the session
    every sixteen seconds - 43 warnings in eleven minutes, and a WebSocket
    that never lived long enough to deliver a single push.
    """

    async def test_the_nudge_goes_out_as_getstates(self, connected):
        await connected._send_status_nudge()

        endpoint = decrypt_frame(connected, sent_frame(connected)).split("\n", 1)[0]
        assert endpoint == "/api/v1/getStates"

    async def test_a_nudge_nobody_answers_is_not_a_problem(self, connected):
        """The whole point. No timeout, no dropped socket, no warning."""
        connected._active_ws.send.side_effect = None  # the lock does not reply

        await connected._send_status_nudge()

        connected._active_ws.close.assert_not_called()
        assert connected._ws_reply is None

    async def test_the_nudge_does_not_occupy_the_order(self, connected):
        """Not registering a slot is only safe because the nudge expects
        nothing back - so nothing is left waiting that a later answer could
        be handed to."""
        connected._active_ws.send.side_effect = None

        await connected._send_status_nudge()

        assert connected._ws_reply is None

    async def test_a_nudge_without_a_socket_does_nothing(self, connected):
        """Reachable when the session drops between the watchdog's check and
        its nudge."""
        connected.ws_connected = False

        await connected._send_status_nudge()

        connected._active_ws.send.assert_not_called()

    async def test_a_broken_socket_during_a_nudge_stays_quiet(self, connected, caplog):
        """The coordinator polls anyway and a socket this far gone is the
        watchdog's business, so this must not add noise of its own."""
        connected._active_ws.send.side_effect = OSError("socket gone")

        await connected._send_status_nudge()

        assert "WARNING" not in caplog.text


class TestOwnFrameDecoding:
    async def test_a_frame_can_be_read_back(self, connected):
        """Used by the tests that check what went on the wire instead of
        trusting a mock - so it needs holding itself."""
        await connected.async_ws_request("/api/v1/control", {"command": "night"})

        read_back = connected._decrypt_own_frame(sent_frame(connected))
        endpoint, body = read_back.split("\n", 1)
        assert endpoint == "/api/v1/control"
        assert json.loads(body) == {"command": "night"}

    async def test_reading_back_without_key_material_is_refused(self, connected):
        frame = connected._encode_ws_request("/api/v1/getStates", {})
        connected.shared_key = None

        with pytest.raises(RuntimeError, match="No key material"):
            connected._decrypt_own_frame(frame)


class TestRequestEdges:
    """The corners of the request path, so a later change cannot quietly
    remove the guards that sit in them."""

    async def test_a_request_without_key_material_is_raised(self, connected):
        """`async_ws_request` is public, so it can be reached in a state the
        command path screens out before it gets there."""
        connected.shared_key = None

        with pytest.raises(RuntimeError, match="before the handshake"):
            await connected.async_ws_request("/api/v1/getParams")

    async def test_dropping_an_already_gone_socket_is_not_an_error(self, connected):
        """The timeout that drops the socket may be racing a close that has
        already happened. Raising there would replace a clear timeout with a
        confusing socket error."""
        connected._active_ws.send.side_effect = None
        connected._active_ws.close.side_effect = OSError("already gone")

        with pytest.raises(TimeoutError):
            await connected.async_ws_request("/api/v1/getParams", timeout=0.01)

    async def test_dropping_with_no_socket_at_all_is_a_no_op(self, connected):
        connected._active_ws = None

        await connected._drop_socket()


class TestReadsOverTheWebSocket:
    """`getSystemState` and `getConfiguration` read over the open socket.

    Both are served over either transport. Using the connection that is
    already open saves a TLS handshake and a TCP session every twelve hours,
    and it is the path 3.0.0 builds on.
    """

    async def test_the_system_state_comes_over_the_websocket(self, connected):
        connected.ws_answer = '{"XC_SUC": {"firmware": "1.6.2", "lock_cnt": 1458}}'

        with patch.object(connected, "_request") as http:
            assert await connected.get_system_state() == {
                "firmware": "1.6.2",
                "lock_cnt": 1458,
            }

        http.assert_not_called()

    async def test_the_configuration_comes_over_the_websocket(self, connected):
        connected.ws_answer = '{"XC_SUC": {"system": {"name": "Haustuer"}}}'

        with patch.object(connected, "_request") as http:
            assert await connected.get_configuration() == {"system": {"name": "Haustuer"}}

        http.assert_not_called()

    async def test_http_serves_the_read_without_a_socket(self, client):
        """Polling mode, or a WebSocket the network blocks."""
        with patch.object(client, "_request", return_value={"firmware": "1.6.2"}) as http:
            assert await client.get_system_state() == {"firmware": "1.6.2"}

        assert http.call_args.args[0] == "/api/v1/getSystemState"

    async def test_a_dead_socket_falls_back_to_http(self, connected):
        connected._active_ws.send.side_effect = OSError("socket gone")

        with patch.object(connected, "_request", return_value={"firmware": "1.6.2"}) as http:
            assert await connected.get_system_state() == {"firmware": "1.6.2"}

        http.assert_called_once()

    async def test_a_refusal_is_not_retried_over_http(self, connected):
        """The lock answered. A second route would get the same answer, and
        for anything with a side effect it would be the second attempt."""
        connected.ws_answer = '{"XC_ERR": {"code": "E_LOCKED"}}'

        with patch.object(connected, "_request") as http:
            with pytest.raises(Exception, match="Device Error: E_LOCKED"):
                await connected.get_system_state()

        http.assert_not_called()


class TestCommandRefusal:
    """A refused command is recorded, not reported.

    No benign refusal code is known yet - the one XC_ERR seen in seven weeks
    of operation arrived without a text and its presumed cause was measured
    and disproved. Surfacing refusals as errors could therefore raise an alarm
    for a command that worked, so for now the log collects the evidence.
    """

    async def test_a_refusal_does_not_become_an_http_retry(self, connected):
        """The lock already carried the command out or refused it. Sending it
        again over HTTP could execute it twice."""
        connected.ws_answer = '{"XC_ERR": {"code": "E_BUSY"}}'

        with patch.object(connected, "_request") as request:
            assert await connected.async_send_payload(
                "/api/v1/control", {"command": "night"}
            ) is True

        request.assert_not_called()

    async def test_a_refusal_is_logged_with_its_code_and_request(self, connected, caplog):
        connected.ws_answer = '{"XC_ERR": {"code": "E_BUSY"}}'

        await connected.async_send_payload("/api/v1/control", {"command": "night"})

        assert "E_BUSY" in caplog.text
        assert "/api/v1/control" in caplog.text
        assert "rejected" in caplog.text.lower()

    async def test_an_unanswered_command_is_not_sent_a_second_time(self, connected):
        """The frame went out. Whether the lock acted on it is unknown, and
        `unlock` pulls the latch - so a retry over HTTP could open the door a
        second time. Before the request layer existed this could not happen,
        because the HTTP route only ran when the send itself failed.
        """
        connected._active_ws.send.side_effect = None  # frame goes out, no answer

        with patch.object(connected, "_request") as http, patch(
            f"{API}.WS_REQUEST_TIMEOUT", 0.01
        ):
            assert await connected.async_send_payload(
                "/api/v1/control", {"command": "unlock"}
            ) is True

        http.assert_not_called()

    async def test_an_unanswered_command_says_so_in_the_log(self, connected, caplog):
        connected._active_ws.send.side_effect = None

        with patch(f"{API}.WS_REQUEST_TIMEOUT", 0.01):
            await connected.async_send_payload("/api/v1/control", {"command": "unlock"})

        assert "no acknowledgement" in caplog.text.lower()
        assert "not repeated" in caplog.text.lower()

    async def test_an_unanswered_read_does_fall_back(self, connected):
        """The asymmetry is deliberate: a read has no side effect, so asking
        again over another transport costs nothing but a round trip."""
        connected._active_ws.send.side_effect = None

        with patch.object(connected, "_request", return_value={"firmware": "1.6.2"}) as http, patch(
            f"{API}.WS_REQUEST_TIMEOUT", 0.01
        ):
            assert await connected.get_system_state() == {"firmware": "1.6.2"}

        http.assert_called_once()

    async def test_a_transport_failure_still_falls_back(self, connected):
        """Unlike a refusal: nothing reached the lock, so HTTP may."""
        connected._active_ws.send.side_effect = OSError("socket gone")

        with patch.object(connected, "_request", return_value={}) as request:
            assert await connected.async_send_payload(
                "/api/v1/control", {"command": "night"}
            ) is True

        request.assert_called_once()


class TestSendWithoutKeyMaterial:
    """`ws_connected` without key material is a broken state, not a bad lock.

    Falling back to HTTP here would paper over it and the next command would
    find the same state again, so this raises instead. Reachable only through
    a bug on our side, which is why it says so in the message.
    """

    async def test_a_missing_shared_key_is_raised_not_hidden(self, connected):
        connected.shared_key = None

        with pytest.raises(RuntimeError, match="before the handshake"):
            await connected.async_send_payload("/api/v1/control", {"command": "day"})

    async def test_a_missing_client_challenge_is_raised_too(self, connected):
        connected.client_challenge = None

        with pytest.raises(RuntimeError, match="before the handshake"):
            await connected.async_send_payload("/api/v1/control", {"command": "day"})


class TestHttpFallback:
    async def test_used_when_no_websocket(self, client):
        with patch.object(client, "_request", return_value={}) as request:
            assert await client.async_send_payload("/api/v1/control", {"command": "day"})

        assert request.call_args.args[0] == "/api/v1/control"

    async def test_used_when_the_websocket_send_fails(self, connected):
        connected._active_ws.send.side_effect = OSError("socket gone")

        with patch.object(connected, "_request", return_value={}) as request:
            assert await connected.async_send_payload("/api/v1/control", {"command": "day"})

        request.assert_called_once()

    async def test_failure_on_both_paths_is_reported(self, connected):
        connected._active_ws.send.side_effect = OSError("socket gone")

        with patch.object(connected, "_request", side_effect=OSError("no route")):
            assert await connected.async_send_payload("/api/v1/control", {"command": "day"}) is False

    async def test_http_only_failure_is_reported(self, client):
        with patch.object(client, "_request", side_effect=OSError("no route")):
            assert await client.async_send_payload("/api/v1/control", {"command": "day"}) is False


# ---------------------------------------------------------------- commands

class TestExecuteCommand:
    @pytest.mark.parametrize(
        ("command", "value", "expected"),
        [
            ("mode", "day", "day"),
            ("mode", "night", "night"),
            ("lock", None, "night"),
            ("unlock", None, "day"),
            ("open", None, "unlock"),
        ],
    )
    async def test_commands_reach_the_lock(self, connected, command, value, expected):
        await connected.async_execute_command(command, value)

        _, payload = decrypt_frame(connected, sent_frame(connected)).split("\n", 1)
        assert json.loads(payload) == {"command": expected}

    @pytest.mark.parametrize(
        ("command", "value"),
        [("mode", None), ("mode", "tag"), ("bogus", None), ("MODE", "day")],
    )
    async def test_invalid_input_never_reaches_the_lock(self, connected, command, value):
        assert await connected.async_execute_command(command, value) is False
        connected._active_ws.send.assert_not_awaited()

    async def test_http_path_fetches_the_new_state(self, client):
        """Without a WebSocket nothing pushes, so the state is pulled once."""
        with patch.object(client, "_request", return_value={}), patch.object(
            client, "get_states_http", return_value=STATES
        ) as get_states, patch("asyncio.sleep", new_callable=AsyncMock):
            received = []
            client.on_state_change = received.append

            await client.async_execute_command("lock")

        get_states.assert_called_once()
        assert received == [STATES]

    async def test_websocket_path_waits_for_the_push(self, connected):
        """The lock reports the change itself, so nothing is pulled."""
        with patch.object(connected, "get_states_http") as get_states:
            await connected.async_execute_command("lock")

        get_states.assert_not_called()

    async def test_no_callback_means_no_crash(self, client):
        with patch.object(client, "_request", return_value={}), patch.object(
            client, "get_states_http", return_value=STATES
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            client.on_state_change = None
            assert await client.async_execute_command("lock")


class TestUnblock:
    async def test_sends_to_the_unblock_endpoint(self, connected):
        assert await connected.async_unblock()

        endpoint, _ = decrypt_frame(connected, sent_frame(connected)).split("\n", 1)
        assert endpoint == "/api/v1/unblock"

    async def test_reports_a_refusal(self, client):
        with patch.object(client, "_request", side_effect=OSError("no route")):
            assert await client.async_unblock() is False


# -------------------------------------------------------------------- stop

class TestStop:
    async def test_closes_the_websocket(self, connected):
        # stop() clears the reference, so hold on to it first
        websocket = connected._active_ws

        await connected.stop()

        websocket.close.assert_awaited_once()
        assert connected.ws_connected is False
        assert connected._active_ws is None

    async def test_clears_the_session(self, connected):
        connected.current_session_start = 1234.0
        await connected.stop()

        assert connected.current_session_start is None

    async def test_stops_the_monitor_loop(self, connected):
        connected._monitor_running = True
        await connected.stop()

        assert connected._monitor_running is False

    async def test_cancels_the_watchdog(self, connected):
        task = asyncio.create_task(asyncio.sleep(3600))
        connected._watchdog_task = task

        await connected.stop()

        assert task.cancelled()
        assert connected._watchdog_task is None

    async def test_survives_a_websocket_that_will_not_close(self, connected):
        connected._active_ws.close.side_effect = OSError("already gone")
        await connected.stop()

        assert connected.ws_connected is False

    async def test_works_without_a_connection(self, client):
        await client.stop()
        assert client._monitor_running is False


class TestSessionOwnership:
    async def test_the_shared_session_is_left_open(self, connected):
        """Home Assistant owns the session; closing it would break every
        other integration sharing it."""
        await connected.stop()

        connected._session.close.assert_not_called()

    async def test_reloading_does_not_exhaust_the_session(self, connected):
        """An entry may be reloaded many times over a Home Assistant run."""
        for _ in range(5):
            await connected.stop()

        connected._session.close.assert_not_called()
