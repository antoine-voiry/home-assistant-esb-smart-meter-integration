"""The ESB Smart Meter integration."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api_client import ESBDataApi
from .const import CONF_MPRN, CONF_PASSWORD, CONF_UPDATE_INTERVAL, CONF_USERNAME, DOMAIN
from .coordinator import ESBDataUpdateCoordinator
from .utils import create_esb_session

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor"]

# This integration is configured via config entries only
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:  # pylint: disable=unused-argument
    """Set up the ESB Smart Meter component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ESB Smart Meter from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Get credentials from config entry
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    mprn = entry.data[CONF_MPRN]
    update_interval_hours = entry.options.get(CONF_UPDATE_INTERVAL, 24)
    update_interval = timedelta(hours=update_interval_hours)

    # Create shared session for this config entry
    session = await create_esb_session(hass)

    # Create API client
    esb_api = ESBDataApi(
        hass=hass,
        session=session,
        username=username,
        password=password,
        mprn=mprn,
    )

    # Create coordinator
    coordinator = ESBDataUpdateCoordinator(
        hass=hass,
        esb_api=esb_api,
        mprn=mprn,
        config_entry=entry,
        update_interval=update_interval,
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator and session for cleanup
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "session": session,
    }

    # Forward setup to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Clean up session if it exists
        entry_data = hass.data[DOMAIN].get(entry.entry_id, {})
        session = entry_data.get("session")
        if session and not session.closed:
            try:
                await session.close()
                _LOGGER.debug("Closed aiohttp session for entry %s", entry.entry_id)
            except Exception as err:
                _LOGGER.warning("Error closing session: %s", err)

        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
