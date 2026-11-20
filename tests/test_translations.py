"""The translation files have to agree with each other and with the code.

Nothing checked this before, and that is how a hardcoded English label sat
in the options flow while every other string was translated: a German user
picked their connection mode in English. A missing key is invisible at
runtime - Home Assistant falls back to the raw value or to English - so it
has to be caught here or not at all.
"""

import json
from pathlib import Path

import pytest

from custom_components.winkhaus_doorclient.const import (
    CONF_UPDATE_MODE,
    MODE_HYBRID,
    MODE_POLLING,
)

COMPONENT = Path("custom_components/winkhaus_doorclient")
SOURCE = COMPONENT / "strings.json"
TRANSLATIONS = sorted((COMPONENT / "translations").glob("*.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def key_paths(obj, prefix=""):
    """Every leaf path in the document, so the shapes can be compared."""
    if isinstance(obj, dict):
        out = set()
        for key, value in obj.items():
            out |= key_paths(value, f"{prefix}/{key}")
        return out
    return {prefix}


def test_every_language_is_present() -> None:
    """en is the fallback, de is the one this lock is mostly sold into."""
    assert {p.name for p in TRANSLATIONS} >= {"en.json", "de.json"}


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_a_translation_matches_the_source_shape(path: Path) -> None:
    """Same keys as strings.json - no missing ones, no leftovers."""
    expected = key_paths(load(SOURCE))
    actual = key_paths(load(path))

    assert not expected - actual, f"{path.name} is missing: {sorted(expected - actual)}"
    assert not actual - expected, f"{path.name} has extra: {sorted(actual - expected)}"


@pytest.mark.parametrize("path", [SOURCE, *TRANSLATIONS], ids=lambda p: p.name)
def test_no_empty_strings(path: Path) -> None:
    """An empty string reads as a bug in the UI, not as a missing label."""
    def walk(obj, prefix=""):
        if isinstance(obj, dict):
            for key, value in obj.items():
                yield from walk(value, f"{prefix}/{key}")
        else:
            assert isinstance(obj, str) and obj.strip(), f"{path.name}{prefix} is empty"

    list(walk(load(path)))


@pytest.mark.parametrize("path", [SOURCE, *TRANSLATIONS], ids=lambda p: p.name)
def test_the_mode_selector_labels_every_stored_value(path: Path) -> None:
    """The dropdown is built from the constants, so the labels must match.

    These keys are the stored tokens, not the names shown: MODE_HYBRID is
    still "hybrid" in every config entry while the label reads "Push". If a
    token is ever renamed, this test is what notices that the label went
    missing with it.
    """
    options = load(path)["selector"][CONF_UPDATE_MODE]["options"]

    assert set(options) == {MODE_HYBRID, MODE_POLLING}


@pytest.mark.parametrize("path", [SOURCE, *TRANSLATIONS], ids=lambda p: p.name)
def test_the_old_name_is_gone_from_the_interface(path: Path) -> None:
    """"Hybrid" described two transports doing two jobs, which stopped being
    true in 3.0.0: everything goes over the socket now and HTTP is only the
    fallback. The word should not survive anywhere a user can read it.
    """
    document = path.read_text(encoding="utf-8")

    assert "Hybrid" not in document
