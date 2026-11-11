"""Configure the authenticated Sunday daemon."""
import asyncio
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from . import DOMAIN


class SundayConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input:
            url = user_input["url"].rstrip("/")
            try:
                session = async_get_clientsession(self.hass)
                async with asyncio.timeout(10):
                    async with session.get(url + "/v1/state", headers={"Authorization": "Bearer " + user_input["token"]}) as response:
                        response.raise_for_status()
                        await response.json()
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Sunday 2.0", data={"url": url, "token": user_input["token"]})
            except Exception:
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("url", default="http://127.0.0.1:8090"): str,
            vol.Required("token"): str,
        }), errors=errors)
