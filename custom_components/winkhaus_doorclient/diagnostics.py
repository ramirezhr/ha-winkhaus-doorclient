# in custom_components/winkhaus_doorclient/diagnostics.py

"""Diagnostics support for the Winkhaus Doorclient integration.

Home Assistant discovers this module automatically and offers a
"Download Diagnostics" button on the device page. No entry in PLATFORMS
and no manifest change is required.
"""

from __future__ import annotations

import time
from typing import Any

import websockets
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_UPDATE_MODE, MODE_HYBRID

# Diagnostics end up pasted into public issue reports, so anything that
# identifies the installation or its network is removed. Redaction is
# recursive by key name, which also covers the nested network block the
# lock reports in its system state.
TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_IP_ADDRESS,
    "serial_number",
    "ip",
    "gw",
    "dns",
    "sn",
}


def _describe_last_request(client) -> dict[str, Any] | None:
    """The most recent WebSocket request, without its payload contents."""
    if not client._last_request:
        return None

    endpoint, payload, sent_at = client._last_request
    return {
        "endpoint": endpoint,
        "payload_keys": sorted(payload) if isinstance(payload, dict) else None,
        "age_seconds": round(time.time() - sent_at, 1),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    stored = hass.data[DOMAIN][entry.entry_id]
    client = stored["client"]
    coordinator = stored["coordinator"]
    system_coordinator = stored["system_coordinator"]
    device_info = stored["device_info"]

    update_mode = entry.options.get(CONF_UPDATE_MODE, MODE_HYBRID)

    return {
        "config": {
            "update_mode": update_mode,
            "options": dict(entry.options),
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "device": {
            "model": device_info.get("model"),
            "sw_version": device_info.get("sw_version"),
        },
        "connection": {
            # How the link is doing right now. connection_count resets when
            # Home Assistant restarts, so read it together with the uptime.
            "websocket_connected": client.ws_connected,
            "connection_count": client.connection_count,
            "current_uptime_seconds": round(client.get_current_uptime(), 1),
            "last_session_seconds": round(client.last_session_seconds, 1),
            "seconds_since_last_message": (
                round(time.time() - client.last_message_time, 1)
                if client.last_message_time
                else None
            ),
            "websockets_version": websockets.__version__,
        },
        "protocol": {
            # Counters and buffers of the hand-rolled WebSocket protocol.
            # A non-empty reassembly buffer outside of a transfer, or a
            # device counter that stopped advancing, points at a problem.
            "client_counter": client.client_counter,
            "device_counter": client._device_counter,
            "reassembly_buffer_bytes": len(client._rx_buffer),
            "reassembly_packet_type": client._rx_type,
            "last_request": _describe_last_request(client),
        },
        "coordinators": {
            "main": {
                "last_update_success": coordinator.last_update_success,
                "update_interval_seconds": (
                    coordinator.update_interval.total_seconds()
                    if coordinator.update_interval
                    else None
                ),
                "data": coordinator.data,
            },
            "system": {
                "last_update_success": system_coordinator.last_update_success,
                "update_interval_seconds": (
                    system_coordinator.update_interval.total_seconds()
                    if system_coordinator.update_interval
                    else None
                ),
                "data": async_redact_data(system_coordinator.data or {}, TO_REDACT),
            },
        },
    }
