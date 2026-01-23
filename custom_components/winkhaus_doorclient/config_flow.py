# in custom_components/winkhaus_doorclient/config_flow.py

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.components.zeroconf import async_get_instance
from zeroconf import ServiceBrowser
import requests
import logging
import asyncio

from .const import DOMAIN
from .api import DoorClient

_LOGGER = logging.getLogger(__name__)

class WinkhausDoorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    VERSION = 1

    def __init__(self):
        self.discovery_info = {}
        self.found_devices = {} 

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(
            step_id="user",
            menu_options=["scan", "manual"]
        )

    async def async_step_scan(self, user_input=None):
        if user_input is None:
            pass

        aio_zc = await async_get_instance(self.hass)
        found = {}

        def on_service_state_change(zeroconf, service_type, name, state_change):
            if state_change.name == "Added":
                info = zeroconf.get_service_info(service_type, name)
                if info:
                    import socket
                    try:
                        ip = socket.inet_ntoa(info.addresses[0])
                        
                        serial = name.split(".")[0] 
                        properties = info.properties
                        for key in [b"serial", b"sn", b"id", b"mac"]:
                            if key in properties:
                                try:
                                    decoded_serial = properties[key].decode("utf-8")
                                    serial = decoded_serial
                                    break
                                except: pass
                        
                        found[serial] = ip
                    except Exception:
                        pass

        browser = ServiceBrowser(aio_zc, "_whdc-device._tcp.local.", handlers=[on_service_state_change])
        
        await asyncio.sleep(3)
        
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
        ip_address = discovery_info.host
        
        serial_number = discovery_info.hostname.split(".")[0]
        for key in ["serial", "sn", "id", "mac"]:
            if key in properties:
                try:
                    val = properties[key]
                    if isinstance(val, bytes): val = val.decode("utf-8")
                    serial_number = val
                    break
                except: continue

        await self.async_set_unique_id(serial_number)
        self._abort_if_unique_id_configured(updates={CONF_IP_ADDRESS: ip_address})

        self.discovery_info = {
            "serial_number": serial_number,
            CONF_IP_ADDRESS: ip_address
        }
        
        self.context["title_placeholders"] = {"serial_number": serial_number}
        return await self.async_step_auth()

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