from unittest.mock import MagicMock
"""Pure logic that needs no running Home Assistant.

Every case here corresponds to a bug that was found in the field, so a
failure means a known problem has come back.
"""

from datetime import timezone

import pytest

from custom_components.winkhaus_doorclient.api import (
    COMMAND_MAP,
    VALID_MODES,
    WINKHAUS_STATUS_MAP,
    DoorClient,
)
from custom_components.winkhaus_doorclient.const import (
    MODEL_BLUEMOTION,
    MODEL_EAV4,
    build_entity_id,
    normalise_serial,
)
from custom_components.winkhaus_doorclient.lock import WinkhausLock

SERIAL = "WH_021C6920F1B188"


@pytest.fixture
def client():
    """A client that never touches the network."""
    return DoorClient(serial_number=SERIAL, ip="10.0.0.1", password="secret", session=MagicMock(), ssl_context=MagicMock())


# ---------------------------------------------------------------- entity ids

class TestEntityId:
    """Ids follow the serial number, not the name configured on the lock."""

    @pytest.mark.parametrize(
        ("platform", "suffix", "expected"),
        [
            ("lock", "lock", "lock.winkhaus_door_wh_021c6920f1b188_lock"),
            ("select", "mode", "select.winkhaus_door_wh_021c6920f1b188_mode"),
            ("sensor", "lock_cnt", "sensor.winkhaus_door_wh_021c6920f1b188_lock_cnt"),
        ],
    )
    def test_schema(self, platform, suffix, expected):
        assert build_entity_id(platform, SERIAL, suffix) == expected

    def test_case_is_normalised(self):
        assert build_entity_id("lock", "WH_ABC", "lock") == build_entity_id(
            "lock", "wh_abc", "lock"
        )

    def test_independent_of_device_name(self):
        """Regression: 2.4.2 - ids used to be derived from the lock's name."""
        assert "haustur" not in build_entity_id("lock", SERIAL, "lock")


# ------------------------------------------------------------------- uptime

class TestUptimeFormat:
    """Hours keep counting past 24 so the string stays parsable."""

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0, "00:00:00"),
            (45, "00:00:45"),
            (3661, "01:01:01"),
            (86399, "23:59:59"),
            (86400, "24:00:00"),
            (174623, "48:30:23"),
            (604800, "168:00:00"),
        ],
    )
    def test_format(self, seconds, expected):
        assert WinkhausLock._format_uptime(seconds) == expected

    @pytest.mark.parametrize("seconds", [86400, 174623, 604800])
    def test_first_field_stays_numeric_past_24h(self, seconds):
        """Regression: str(timedelta) produced '2 days, 0:30:23' here."""
        assert int(WinkhausLock._format_uptime(seconds).split(":")[0]) >= 24


# ---------------------------------------------------------------- timestamps

class TestDeviceTime:
    """The lock sends a plain UTC timestamp."""

    def test_carries_explicit_utc_offset(self):
        """Regression: a naive string was rendered as local time, 2h off."""
        result = WinkhausLock._device_time_to_iso(1787486008)
        assert result.endswith("+00:00")

    def test_matches_the_locks_own_localtime(self):
        """getSystemState reported time=1787486008 and localtime 13:53:28 CEST."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        parsed = datetime.fromisoformat(WinkhausLock._device_time_to_iso(1787486008))
        berlin = parsed.astimezone(ZoneInfo("Europe/Berlin"))
        assert berlin.strftime("%Y-%m-%dT%H:%M:%S") == "2026-08-23T13:53:28"

    def test_accepts_a_string(self):
        assert WinkhausLock._device_time_to_iso("1787486008") is not None

    @pytest.mark.parametrize("value", [99999999999999999999, "not a number", None])
    def test_implausible_values_yield_none(self, value):
        assert WinkhausLock._device_time_to_iso(value) is None


# ------------------------------------------------------------------ commands

class TestCommandResolution:
    """Every entity action maps to a command the lock understands."""

    @pytest.mark.parametrize(
        ("command", "value", "expected"),
        [
            ("mode", "day", "day"),
            ("mode", "night", "night"),
            ("night", None, "night"),
            ("day", None, "day"),
            ("open", None, "unlock"),
            ("lock", None, "night"),
            ("unlock", None, "day"),
        ],
    )
    def test_known_commands(self, command, value, expected):
        resolved = value if command == "mode" else COMMAND_MAP.get(command)
        assert resolved == expected

    @pytest.mark.parametrize("value", [None, "", "tag", "DAY", 1])
    def test_invalid_mode_is_rejected(self, value):
        assert value not in VALID_MODES

    @pytest.mark.parametrize("command", ["bogus", "MODE", "", "Open"])
    def test_unknown_command_is_rejected(self, command):
        assert COMMAND_MAP.get(command) is None

    def test_open_is_not_unlock(self):
        """The lock's 'unlock' pulls the latch; HA's 'unlock' means day mode."""
        assert COMMAND_MAP["open"] == "unlock"
        assert COMMAND_MAP["unlock"] == "day"


# -------------------------------------------------------------- state parsing

class TestFormatStates:
    """Numeric indices are mapped, booleans must not be."""

    def test_strings_pass_through(self, client):
        result = client._format_states({"state": "closed", "mode": "day"})
        assert {"name": "state", "value": "closed"} in result

    def test_numeric_indices_are_mapped(self, client):
        result = client._format_states({"state": 0, "mode": 1})
        values = {i["name"]: i["value"] for i in result}
        assert values == {"state": "open", "mode": "night"}

    def test_booleans_are_left_alone(self, client):
        """bool is a subclass of int - locked must never be index-mapped."""
        result = client._format_states({"locked": False, "state": "closed"})
        values = {i["name"]: i["value"] for i in result}
        assert values["locked"] is False

    def test_locked_is_not_in_the_map(self):
        """The original defines locked as ['true','false'] - index 0 is 'true'.

        Adding it here would invert the lock state, because False maps to
        index 0. Guarded by a test so nobody adds it in good faith.
        """
        assert "locked" not in WINKHAUS_STATUS_MAP

    def test_out_of_range_index_is_kept(self, client):
        result = client._format_states({"state": 99})
        assert result == [{"name": "state", "value": 99}]

    def test_non_dict_yields_empty_list(self, client):
        assert client._format_states(None) == []
        assert client._format_states("nonsense") == []


# ------------------------------------------------------------------- uptime 2

class TestSessionTracking:
    def test_no_session_means_zero(self, client):
        assert client.get_current_uptime() == 0.0

    def test_counts_from_session_start(self, client):
        import time

        client.current_session_start = time.time() - 100
        assert 99 <= client.get_current_uptime() <= 101


# --------------------------------------------------------- serial numbers

class TestSerialNormalisation:
    """What someone types versus what goes into the PBKDF2 salt.

    The serial number is key material, so a difference of one character is
    a failed handshake rather than a helpful error. The two mistakes worth
    absorbing are a missing model prefix and stray whitespace.
    """

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            (MODEL_BLUEMOTION, "WH_021C6920F1B188"),
            (MODEL_EAV4, "WH_011C6920F1B188"),
        ],
    )
    def test_the_model_supplies_the_prefix(self, model, expected):
        assert normalise_serial("1C6920F1B188", model) == expected

    def test_a_prefix_already_there_is_kept(self):
        assert normalise_serial("WH_021C6920F1B188", MODEL_EAV4) == "WH_021C6920F1B188"

    def test_an_unknown_prefix_is_not_second_guessed(self):
        """A future model has to work without a release of this integration."""
        assert normalise_serial("WH_09FUTURE0001", MODEL_BLUEMOTION) == "WH_09FUTURE0001"

    def test_the_prefix_is_case_corrected(self):
        """WH_ is a literal we know. The rest is device data and stays put."""
        assert normalise_serial("wh_021c6920f1b188") == "WH_021c6920f1b188"

    @pytest.mark.parametrize(
        "typed", [" WH_021C6920F1B188", "WH_021C6920F1B188 ", "WH_02 1C6920F1B188"]
    )
    def test_whitespace_survives_copy_and_paste_but_not_this(self, typed):
        assert normalise_serial(typed) == "WH_021C6920F1B188"

    def test_without_a_model_nothing_is_invented(self):
        """The flow validates against the lock; guessing would hide the error."""
        assert normalise_serial("1C6920F1B188") == "1C6920F1B188"


# ------------------------------------------------- translation file rules

class TestTranslationRules:
    """The hassfest rules we have tripped over, checked before the CI does.

    Both of these reached main once: a placeholder wrapped in single quotes,
    and a URL written into a description. Neither is visible in review, and
    the nightly run reports them a day later.
    """

    @staticmethod
    def _strings(name):
        import json
        import pathlib

        base = pathlib.Path(__file__).parent.parent / "custom_components" / "winkhaus_doorclient"
        return json.loads((base / name).read_text(encoding="utf-8"))

    @staticmethod
    def _walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                yield from TestTranslationRules._walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, str):
            yield path, node

    FILES = ["strings.json", "translations/en.json", "translations/de.json"]

    @pytest.mark.parametrize("name", FILES)
    def test_no_placeholder_sits_in_single_quotes(self, name):
        """ICU reads a single quote as an escape, so the placeholder dies."""
        import re

        offenders = [
            path
            for path, text in self._walk(self._strings(name))
            if re.search(r"'[^']*\{[^}]*\}[^']*'", text)
        ]
        assert not offenders, offenders

    @pytest.mark.parametrize("name", FILES)
    def test_no_url_is_written_into_a_string(self, name):
        """Links belong in description_placeholders, not in a translation."""
        offenders = [
            path for path, text in self._walk(self._strings(name)) if "http://" in text or "https://" in text
        ]
        assert not offenders, offenders

    def test_strings_and_english_stay_identical(self):
        import pathlib

        base = pathlib.Path(__file__).parent.parent / "custom_components" / "winkhaus_doorclient"
        assert (base / "strings.json").read_text(encoding="utf-8") == (
            base / "translations" / "en.json"
        ).read_text(encoding="utf-8")

    def test_every_language_offers_the_same_keys(self):
        """A key only in one file is a string someone will never see."""
        def keys(name):
            return {path for path, _ in self._walk(self._strings(name))}

        assert keys("strings.json") == keys("translations/de.json")
