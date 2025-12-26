# in custom_components/winkhaus_door/config_flow.py

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
import homeassistant.helpers.config_validation as cv
import requests
import logging

from .const import DOMAIN
from .api import DoorClient

_LOGGER = logging.getLogger(__name__)

class WinkhausDoorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input["serial_number"])
            self._abort_if_unique_id_configured()

            try:
                client = await self.hass.async_add_executor_job(
                    lambda: DoorClient(
                        serial_number=user_input["serial_number"],
                        ip=user_input[CONF_IP_ADDRESS],
                        password=user_input[CONF_PASSWORD],
                        username=user_input[CONF_USERNAME]
                    )
                )

                if not await self.hass.async_add_executor_job(client.connect):
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title=f"Winkhaus Door ({user_input['serial_number']})",
                        data=user_input
                    )

            except requests.exceptions.HTTPError as err:
                if err.response.status_code == 401:
                    _LOGGER.error("Authentifizierung fehlgeschlagen: Benutzername oder Passwort falsch.")
                    errors["base"] = "invalid_auth"
                else:
                    _LOGGER.error(f"HTTP-Fehler beim Verbindungsversuch: {err}")
                    errors["base"] = "cannot_connect"
            except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError, requests.exceptions.SSLError):
                _LOGGER.error("Verbindungsfehler: IP-Adresse ist falsch, Gerät nicht erreichbar oder SSL-Problem.")
                errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.exception(f"Unerwarteter Fehler während der Einrichtung: {e}")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("serial_number"): cv.string,
                vol.Required(CONF_IP_ADDRESS): cv.string,
                vol.Required(CONF_USERNAME, default="admin"): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }),
            errors=errors,
            last_step=False
        )
