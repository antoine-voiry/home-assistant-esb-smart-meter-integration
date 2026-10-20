"""The ESB Smart Meter integration."""

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .api_client import ESBDataApi
from .const import CONF_MPRN, CONF_PASSWORD, CONF_UPDATE_INTERVAL, CONF_USERNAME, DOMAIN
from .coordinator import ESBConfigEntry, ESBDataUpdateCoordinator, ESBRuntimeData
from .import_state import COST_SERIES, ImportState
from .utils import create_esb_session

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor"]

# This integration is configured via config entries only
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ESBConfigEntry) -> bool:
    """Set up ESB Smart Meter from a config entry."""
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

    # Fetch initial data; HA retries setup on failure, so don't leak a session each time
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await session.close()
        raise

    entry.runtime_data = ESBRuntimeData(coordinator=coordinator, session=session)

    # Options and tariff subentry changes take effect through a reload
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))

    # Forward setup to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_reload_on_update(hass: HomeAssistant, entry: ESBConfigEntry) -> None:
    """Reload so changed options and tariff periods take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ESBConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        session = entry.runtime_data.session
        if not session.closed:
            try:
                await session.close()
                _LOGGER.debug("Closed aiohttp session for entry %s", entry.entry_id)
            except Exception as err:
                _LOGGER.warning("Error closing session: %s", err)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ESBConfigEntry) -> None:
    """Delete the per-series import state when the integration is removed."""
    for series in COST_SERIES:
        await ImportState(hass, entry.entry_id, series).async_remove()
