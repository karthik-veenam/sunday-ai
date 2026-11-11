"""HA lifecycle adapter; the persistent brain runs outside Home Assistant."""
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv

DOMAIN = "sunday2"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.CONVERSATION])
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, [Platform.CONVERSATION])
