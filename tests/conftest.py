"""Shared fixtures."""

from unittest.mock import MagicMock, patch

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make the integration discoverable in every test."""
    yield


@pytest.fixture(autouse=True)
def stub_shared_session():
    """Hand out a dummy instead of Home Assistant's real HTTP session.

    A real session brings up an async DNS resolver whose background thread
    outlives the test, which the framework's cleanup check reports as a
    leak. No test here performs an actual request.
    """
    with patch(
        "custom_components.winkhaus_doorclient.coordinator.async_get_clientsession",
        return_value=MagicMock(),
    ):
        yield
