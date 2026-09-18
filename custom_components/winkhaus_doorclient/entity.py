# in custom_components/winkhaus_doorclient/entity.py

"""Shared base class for coordinator-driven Winkhaus entities."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import build_entity_id
from .coordinator import WinkhausConfigEntry


class WinkhausEntity[C: DataUpdateCoordinator[Any]](CoordinatorEntity[C]):
    """Wires up identity and the link to the device registry.

    Every entity derives the same three things from the serial number: a
    unique id, an entity id and the device info. Doing that in one place
    keeps the entity id schema consistent.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: C,
        entry: WinkhausConfigEntry,
        platform: str,
        key: str,
        *,
        unique_key: str | None = None,
        translation_key: str | None = None,
    ) -> None:
        """Set up identity from the serial number.

        `key` is the entity id suffix and doubles as the translation key.

        `unique_key` exists because the unique ids grew before the entity
        ids did and do not always match them - the lock uses the bare serial
        number, the door sensor "door_state" against an entity id of "door".
        Changing them would orphan the registry entries of every existing
        installation, so they are preserved as they are.
        """
        super().__init__(coordinator)

        serial = entry.data["serial_number"]
        if unique_key is None:
            self._attr_unique_id = f"{serial}_{key}"
        elif unique_key == "":
            self._attr_unique_id = serial
        else:
            self._attr_unique_id = f"{serial}_{unique_key}"

        self.entity_id = build_entity_id(platform, serial, key)
        self._attr_translation_key = translation_key or key
        self._attr_device_info = entry.runtime_data.device_info

    @property
    def client(self) -> Any:
        """The API client shared by every entity of this lock."""
        return self.coordinator.client  # type: ignore[attr-defined]

    def state_value(self, name: str, default: Any = None) -> Any:
        """Read one field from the coordinator's list of name/value pairs."""
        if not self.coordinator.data:
            return default
        return next(
            (
                item["value"]
                for item in self.coordinator.data
                if item["name"] == name
            ),
            default,
        )
