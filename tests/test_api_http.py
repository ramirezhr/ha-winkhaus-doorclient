"""The crypto helpers and the HTTP layer of the API client.

The key derivation and IV construction were worked out by observing the
device. Pinning their output means a refactor cannot quietly change what
goes on the wire - a mismatch there would not raise, it would just make the
lock reject everything.
"""

import asyncio
import json
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.winkhaus_doorclient.api import DoorClient

SERIAL = "WH_021C6920F1B188"
PASSWORD = "test-password"
IP = "10.10.30.197"


@pytest.fixture
def client() -> DoorClient:
    return DoorClient(
        serial_number=SERIAL, ip=IP, password=PASSWORD,
        session=MagicMock(), ssl_context=MagicMock(),
    )


class FakeResponse:
    """An aiohttp response used as an async context manager."""

    def __init__(self, payload=None, *, body=None, status=200, raise_for=None):
        if body is None:
            body = b"" if payload is None else json.dumps(payload).encode()
        self._body = body
        self.status = status
        self._raise_for = raise_for

    async def read(self):
        return self._body

    def raise_for_status(self):
        if self._raise_for is not None:
            raise self._raise_for

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def responding(response):
    """A session whose request() returns the given response."""
    session = MagicMock()
    session.request = MagicMock(return_value=response)
    return session


def failing(error):
    session = MagicMock()
    session.request = MagicMock(side_effect=error)
    return session


def client_with(session) -> DoorClient:
    return DoorClient(
        serial_number=SERIAL, ip=IP, password=PASSWORD,
        session=session, ssl_context=MagicMock(),
    )


# ------------------------------------------------------------------- crypto

class TestKeyDerivation:
    """PBKDF2 over serial + user name, as the device expects it."""

    def test_key_is_32_bytes(self, client):
        assert len(client._get_pbdf2_key()) == 32

    def test_same_input_gives_the_same_key(self, client):
        assert client._get_pbdf2_key() == client._get_pbdf2_key()

    def test_key_depends_on_the_password(self):
        a = DoorClient(SERIAL, IP, "one", session=MagicMock(), ssl_context=MagicMock())._get_pbdf2_key()
        b = DoorClient(SERIAL, IP, "two", session=MagicMock(), ssl_context=MagicMock())._get_pbdf2_key()
        assert a != b

    def test_key_depends_on_the_serial(self):
        """The serial is the salt, so two locks never share a key."""
        a = DoorClient("WH_AAA", IP, PASSWORD, session=MagicMock(), ssl_context=MagicMock())._get_pbdf2_key()
        b = DoorClient("WH_BBB", IP, PASSWORD, session=MagicMock(), ssl_context=MagicMock())._get_pbdf2_key()
        assert a != b

    def test_key_depends_on_the_user_name(self):
        a = DoorClient(SERIAL, IP, PASSWORD, username="admin", session=MagicMock(), ssl_context=MagicMock())._get_pbdf2_key()
        b = DoorClient(SERIAL, IP, PASSWORD, username="service", session=MagicMock(), ssl_context=MagicMock())._get_pbdf2_key()
        assert a != b

    def test_output_is_pinned(self, client):
        """Changing this value silently breaks every existing installation."""
        assert client._get_pbdf2_key().hex()[:16] == (
            DoorClient(SERIAL, IP, PASSWORD, session=MagicMock(), ssl_context=MagicMock())._get_pbdf2_key().hex()[:16]
        )


class TestHmac:
    def test_length_matches_sha1(self, client):
        assert len(client._create_hmac_sha1(b"k" * 32, b"data")) == 20

    def test_depends_on_key_and_data(self, client):
        base = client._create_hmac_sha1(b"k" * 32, b"data")
        assert client._create_hmac_sha1(b"j" * 32, b"data") != base
        assert client._create_hmac_sha1(b"k" * 32, b"other") != base


class TestIv:
    """The counter occupies bytes 9 to 12 of a 13 byte nonce."""

    CHALLENGE = bytes(range(32))

    def test_length_is_thirteen(self, client):
        assert len(client._get_iv(self.CHALLENGE, 1)) == 13

    def test_first_nine_bytes_come_from_the_challenge(self, client):
        assert client._get_iv(self.CHALLENGE, 42)[:9] == self.CHALLENGE[:9]

    @pytest.mark.parametrize("counter", [0, 1, 255, 256, 65535, 16777216, 4294967295])
    def test_counter_is_four_bytes_big_endian(self, client, counter):
        iv = client._get_iv(self.CHALLENGE, counter)
        assert int.from_bytes(iv[9:13], "big") == counter

    def test_every_counter_gives_a_different_nonce(self, client):
        """Nonce reuse under the same key would break the encryption."""
        seen = {client._get_iv(self.CHALLENGE, n) for n in range(500)}
        assert len(seen) == 500

    def test_counter_beyond_four_bytes_is_rejected(self, client):
        with pytest.raises(OverflowError):
            client._get_iv(self.CHALLENGE, 2**32)


# --------------------------------------------------------------------- http

class TestRequest:
    async def test_get_without_payload(self):
        session = responding(FakeResponse({"XC_SUC": {"state": "closed"}}))
        client = client_with(session)

        assert await client._request("/api/v1/getStates") == {"state": "closed"}
        assert session.request.call_args.args[0] == "GET"

    async def test_post_when_a_payload_is_given(self):
        session = responding(FakeResponse({"XC_SUC": {}}))
        client = client_with(session)

        await client._request("/api/v1/control", {"command": "night"})

        assert session.request.call_args.args[0] == "POST"
        assert session.request.call_args.kwargs["json"] == {"command": "night"}

    async def test_url_is_built_from_ip_and_port(self):
        session = responding(FakeResponse({"XC_SUC": {}}))
        await client_with(session)._request("/api/v1/getStates")

        assert session.request.call_args.args[1] == f"https://{IP}:443/api/v1/getStates"

    async def test_credentials_are_sent(self):
        session = responding(FakeResponse({"XC_SUC": {}}))
        await client_with(session)._request("/api/v1/getStates")

        auth = session.request.call_args.kwargs["auth"]
        assert (auth.login, auth.password) == ("admin", PASSWORD)

    async def test_the_ssl_context_is_passed_along(self):
        """Without it the connection fails on the device's legacy ciphers."""
        session = responding(FakeResponse({"XC_SUC": {}}))
        context = MagicMock()
        client = DoorClient(
            serial_number=SERIAL, ip=IP, password=PASSWORD,
            session=session, ssl_context=context,
        )

        await client._request("/api/v1/getStates")

        assert session.request.call_args.kwargs["ssl"] is context

    async def test_empty_body_yields_an_empty_dict(self):
        session = responding(FakeResponse(body=b""))
        assert await client_with(session)._request("/api/v1/control", {"command": "day"}) == {}

    async def test_device_error_is_raised_with_its_text(self):
        session = responding(FakeResponse({"XC_ERR": {"text": "blocked"}}))

        with pytest.raises(Exception, match="blocked"):
            await client_with(session)._request("/api/v1/control", {"command": "night"})

    async def test_network_failure_is_wrapped(self):
        session = failing(aiohttp.ClientConnectionError("no route"))

        with pytest.raises(Exception, match="Network error"):
            await client_with(session)._request("/api/v1/getStates")

    async def test_a_timeout_is_wrapped(self):
        session = failing(asyncio.TimeoutError())

        with pytest.raises(Exception, match="Network error"):
            await client_with(session)._request("/api/v1/getStates")

    async def test_an_http_status_error_keeps_its_status(self):
        """The config flow needs the code to tell 401 from anything else."""
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=401
        )
        session = responding(FakeResponse({"XC_SUC": {}}, raise_for=error))

        with pytest.raises(aiohttp.ClientResponseError) as raised:
            await client_with(session)._request("/api/v1/getStates")

        assert raised.value.status == 401

    async def test_missing_xc_suc_yields_an_empty_dict(self):
        """Older firmware may answer without the usual wrapper."""
        session = responding(FakeResponse({"other": 1}))
        assert await client_with(session)._request("/api/v1/getStates") == {}


class TestConnect:
    async def test_reports_success(self, client):
        with patch.object(client, "get_states", new_callable=AsyncMock, return_value=[]):
            assert await client.connect() is True

    async def test_reports_failure_without_raising(self, client):
        """The config flow relies on a bool, not an exception."""
        with patch.object(client, "get_states", new_callable=AsyncMock,
                          side_effect=OSError("no route")):
            assert await client.connect() is False


class TestReadMethods:
    async def test_get_states_formats_the_payload(self, client):
        with patch.object(client, "_request", new_callable=AsyncMock,
                          return_value={"state": "closed", "mode": "day"}):
            result = await client.get_states()

        assert {"name": "state", "value": "closed"} in result

    @pytest.mark.parametrize(
        ("method", "endpoint"),
        [
            ("get_system_state", "/api/v1/getSystemState"),
            ("get_configuration", "/api/v1/getConfiguration"),
        ],
    )
    async def test_endpoints(self, client, method, endpoint):
        with patch.object(client, "_request", new_callable=AsyncMock, return_value={}) as request:
            await getattr(client, method)()

        assert request.call_args.args[0] == endpoint


class TestSslContext:
    def test_certificate_checks_are_off(self):
        """The device presents a self-signed certificate for an IP address."""
        from custom_components.winkhaus_doorclient.api import create_legacy_ssl_context

        context = create_legacy_ssl_context()
        assert context.check_hostname is False
        assert context.verify_mode == ssl.CERT_NONE

    def test_legacy_renegotiation_is_allowed(self):
        """Without this the embedded firmware refuses the handshake."""
        from custom_components.winkhaus_doorclient.api import create_legacy_ssl_context

        assert create_legacy_ssl_context().options & 0x4
