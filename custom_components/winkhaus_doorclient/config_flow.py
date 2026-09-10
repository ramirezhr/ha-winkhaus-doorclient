import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.components.zeroconf import async_get_instance
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from zeroconf import ServiceBrowser
import requests
import logging
import asyncio
import socket

from .const import (
    DOMAIN, 
    CONF_SCAN_INTERVAL, 
    DEFAULT_SCAN_INTERVAL,
    CONF_UPDATE_MODE,
    MODE_HYBRID,
    MODE_POLLING
)
from .api import DoorClient

_LOGGER = logging.getLogger(__name__)

# How long to listen for announcements during a manual network scan.
DISCOVERY_TIMEOUT = 3

# Property keys a lock may publish its serial number under. Firmware
# versions differ, so all of them are tried in order.
SERIAL_PROPERTY_KEYS = (b"serial", b"sn", b"id", b"mac")


def parse_discovered_device(name: str, info) -> tuple[str, str] | None:
    """Turn a zeroconf service record into a (serial, ip) pair.

    Returns None when the record carries no usable address. The service
    name is the fallback serial for firmware that publishes none.
    """
    if not info or not getattr(info, "addresses", None):
        return None

    try:
        ip = socket.inet_ntoa(info.addresses[0])
    except (OSError, TypeError, ValueError):
        return None

    serial = name.split(".")[0]
    properties = getattr(info, "properties", None) or {}

    for key in SERIAL_PROPERTY_KEYS:
        raw = properties.get(key)
        if raw is None:
            continue
        try:
            serial = raw.decode("utf-8")
            break
        except (AttributeError, UnicodeDecodeError):
            continue

    return serial, ip

class WinkhausDoorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    VERSION = 1

    def __init__(self):
        self.discovery_info = {}
        self.found_devices = {} 
        self.reauth_entry = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return WinkhausOptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(
            step_id="user",
            menu_options=["scan", "manual"]
        )

    async def async_step_scan(self, user_input=None):
        aio_zc = await async_get_instance(self.hass)
        found = {}

        def on_service_state_change(zeroconf, service_type, name, state_change):
            if state_change.name != "Added":
                return
            parsed = parse_discovered_device(
                name, zeroconf.get_service_info(service_type, name)
            )
            if parsed:
                found[parsed[0]] = parsed[1]

        browser = ServiceBrowser(
            aio_zc, "_whdc-device._tcp.local.", handlers=[on_service_state_change]
        )

        try:
            await asyncio.sleep(DISCOVERY_TIMEOUT)
        finally:
            browser.cancel()
        
        self.found_devices = found
        
        if not found:
            return self.async_abort(reason="no_devices_found")
            
        return await self.async_step_pick()

    async def async_step_pick(self, user_input=None):
        if user_input is not None:
            serial = user_input["device"]
            ip = self.found_devices[serial]
            
            self.discovery_info = {
                "serial_number": serial,
                CONF_IP_ADDRESS: ip
            }
            return await self.async_step_auth()

        device_options = {
            serial: f"Winkhaus Door {serial} ({ip})" 
            for serial, ip in self.found_devices.items()
        }

        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema({
                vol.Required("device", default=list(device_options.keys())[0]): vol.In(device_options)
            })
        )

    async def async_step_auth(self, user_input=None):
        errors = {}
        serial = self.discovery_info.get("serial_number", "Unknown")
        
        if user_input is not None:
            full_data = {
                **self.discovery_info,
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD]
            }
            return await self._validate_and_create(full_data)

        return self.async_show_form(
            step_id="auth",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME, default="admin"): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }),
            description_placeholders={"serial_number": serial},
            errors=errors
        )

    async def async_step_manual(self, user_input=None):
        if user_input is not None:
            return await self._validate_and_create(user_input)

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({
                vol.Required("serial_number"): cv.string,
                vol.Required(CONF_IP_ADDRESS): cv.string,
                vol.Required(CONF_USERNAME, default="admin"): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }),
            last_step=False
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo):
        properties = discovery_info.properties
        serial_number = properties.get("serial_number") or properties.get("serial")
        ip_address = discovery_info.host

        if not serial_number:
            return self.async_abort(reason="no_serial_in_zeroconf")

        await self.async_set_unique_id(serial_number)
        
        self._abort_if_unique_id_configured(updates={
            CONF_IP_ADDRESS: ip_address
        })
        self.discovery_info = {
            "serial_number": serial_number,
            CONF_IP_ADDRESS: ip_address,
        }
        
        self.context["title_placeholders"] = {"serial_number": serial_number}

        return await self.async_step_auth()

    async def async_step_reauth(self, entry_data: dict):
        self.reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}

        if user_input is not None:
            existing_data = self.reauth_entry.data
            password = user_input[CONF_PASSWORD]

            try:
                client = await self.hass.async_add_executor_job(
                    lambda: DoorClient(
                        serial_number=existing_data["serial_number"],
                        ip=existing_data[CONF_IP_ADDRESS],
                        password=password,
                        username=existing_data[CONF_USERNAME]
                    )
                )

                if await self.hass.async_add_executor_job(client.connect):
                    self.hass.config_entries.async_update_entry(
                        self.reauth_entry,
                        data={
                            **existing_data,
                            CONF_PASSWORD: password
                        }
                    )
                    self.hass.async_create_task(
                        self.hass.config_entries.async_reload(self.reauth_entry.entry_id)
                    )
                    return self.async_abort(reason="reauth_successful")
                else:
                    errors["base"] = "cannot_connect"

            except requests.exceptions.HTTPError as err:
                if err.response.status_code == 401:
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_PASSWORD): cv.string,
            }),
            description_placeholders={
                "username": self.reauth_entry.data.get(CONF_USERNAME)
            },
            errors=errors
        )

    async def async_step_reconfigure(self, user_input=None):
        """Change connection settings of an existing entry.

        Zeroconf already updates the IP on its own, but mDNS does not cross
        subnet boundaries - which is exactly the setup where a lock sits in
        its own IoT VLAN. Without this step the only way to follow a changed
        address was to delete and re-add the integration, losing entity ids,
        history and every automation referring to them.

        The serial number stays fixed: it is the unique id of the entry and
        the basis of every entity id. A different serial is a different lock,
        and therefore a different entry.
        """
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors = {}

        if user_input is not None:
            # An empty password field means "keep the current one", so the
            # stored secret never has to be shown in the form.
            password = user_input.get(CONF_PASSWORD) or entry.data[CONF_PASSWORD]

            new_data = {
                **entry.data,
                CONF_IP_ADDRESS: user_input[CONF_IP_ADDRESS],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: password,
            }

            try:
                client = await self.hass.async_add_executor_job(
                    lambda: DoorClient(
                        serial_number=new_data["serial_number"],
                        ip=new_data[CONF_IP_ADDRESS],
                        password=new_data[CONF_PASSWORD],
                        username=new_data[CONF_USERNAME]
                    )
                )

                # Verify before storing, so a typo cannot replace a working
                # address with an unreachable one.
                if await self.hass.async_add_executor_job(client.connect):
                    self.hass.config_entries.async_update_entry(entry, data=new_data)
                    self.hass.async_create_task(
                        self.hass.config_entries.async_reload(entry.entry_id)
                    )
                    return self.async_abort(reason="reconfigure_successful")
                else:
                    errors["base"] = "cannot_connect"

            except requests.exceptions.HTTPError as err:
                if err.response.status_code == 401:
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_IP_ADDRESS,
                    default=entry.data[CONF_IP_ADDRESS]
                ): cv.string,
                vol.Required(
                    CONF_USERNAME,
                    default=entry.data[CONF_USERNAME]
                ): cv.string,
                vol.Optional(CONF_PASSWORD): cv.string,
            }),
            description_placeholders={
                "serial_number": entry.data["serial_number"]
            },
            errors=errors
        )

    async def _validate_and_create(self, data):
        errors = {}
        await self.async_set_unique_id(data["serial_number"])
        self._abort_if_unique_id_configured()

        try:
            client = await self.hass.async_add_executor_job(
                lambda: DoorClient(
                    serial_number=data["serial_number"],
                    ip=data[CONF_IP_ADDRESS],
                    password=data[CONF_PASSWORD],
                    username=data[CONF_USERNAME]
                )
            )

            if not await self.hass.async_add_executor_job(client.connect):
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"Winkhaus Door ({data['serial_number']})",
                    data=data
                )

        except requests.exceptions.HTTPError as err:
            if err.response.status_code == 401:
                errors["base"] = "invalid_auth"
            else:
                errors["base"] = "cannot_connect"
        except Exception:
            errors["base"] = "unknown"
            
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({
                vol.Required("serial_number", default=data["serial_number"]): cv.string,
                vol.Required(CONF_IP_ADDRESS, default=data[CONF_IP_ADDRESS]): cv.string,
                vol.Required(CONF_USERNAME, default=data[CONF_USERNAME]): cv.string,
                vol.Required(CONF_PASSWORD, default=data[CONF_PASSWORD]): cv.string,
            }),
            errors=errors
        )


class WinkhausOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        # Use self._entry instead of self.config_entry (reserved by HA core)
        self._entry = config_entry
        self.options = dict(config_entry.options)

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            self.options.update(user_input)
            if user_input[CONF_UPDATE_MODE] == MODE_POLLING:
                return await self.async_step_polling()
            
            return self.async_create_entry(title="", data=self.options)

        current_mode = self.options.get(CONF_UPDATE_MODE, MODE_HYBRID)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_UPDATE_MODE, default=current_mode): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": MODE_HYBRID, "label": "Hybrid (WebSockets + Fallback)"},
                            {"value": MODE_POLLING, "label": "Classic Polling (HTTP only)"}
                        ],
                        mode=SelectSelectorMode.DROPDOWN
                    )
                )
            })
        )

    async def async_step_polling(self, user_input=None):
        if user_input is not None:
            self.options.update(user_input)
            return self.async_create_entry(title="", data=self.options)

        current_interval = self.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        if current_interval < 30: current_interval = 30

        return self.async_show_form(
            step_id="polling",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_SCAN_INTERVAL, 
                    default=current_interval
                ): NumberSelector(NumberSelectorConfig(
                    min=30, 
                    max=300, 
                    step=1, 
                    mode=NumberSelectorMode.SLIDER,
                    unit_of_measurement="s"
                )),
            })
        )