"""Support for ESB Smart Meter sensors."""

import asyncio
import logging
from abc import abstractmethod

import aiohttp
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api_client import ESBDataApi
from .cache import ESBCachingApi
from .const import (CONF_MPRN, CONF_PASSWORD, CONF_USERNAME, DOMAIN,
                    MANUFACTURER, MODEL)
from .models import ESBData
from .utils import create_esb_session, get_startup_delay

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ESB Smart Meter sensor based on a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    mprn = entry.data[CONF_MPRN]

    session = await create_esb_session(hass)

    # Store session reference for cleanup
    if entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN][entry.entry_id]["session"] = session

    # Calculate startup delay once for all sensors
    startup_delay = await get_startup_delay(hass)

    esb_api = ESBCachingApi(
        ESBDataApi(
            hass=hass,
            session=session,
            username=username,
            password=password,
            mprn=mprn,
        )
    )

    sensors = [
        TodaySensor(
            esb_api=esb_api,
            mprn=mprn,
            name="ESB Electricity Usage: Today",
            startup_delay=startup_delay,
        ),
        Last24HoursSensor(
            esb_api=esb_api,
            mprn=mprn,
            name="ESB Electricity Usage: Last 24 Hours",
            startup_delay=startup_delay,
        ),
        ThisWeekSensor(
            esb_api=esb_api,
            mprn=mprn,
            name="ESB Electricity Usage: This Week",
            startup_delay=startup_delay,
        ),
        Last7DaysSensor(
            esb_api=esb_api,
            mprn=mprn,
            name="ESB Electricity Usage: Last 7 Days",
            startup_delay=startup_delay,
        ),
        ThisMonthSensor(
            esb_api=esb_api,
            mprn=mprn,
            name="ESB Electricity Usage: This Month",
            startup_delay=startup_delay,
        ),
        Last30DaysSensor(
            esb_api=esb_api,
            mprn=mprn,
            name="ESB Electricity Usage: Last 30 Days",
            startup_delay=startup_delay,
        ),
    ]

    # Don't update before add - let the startup delay handle first update
    async_add_entities(sensors, False)


class BaseSensor(SensorEntity):
    """Base sensor class for ESB Smart Meter sensors."""

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:flash"

    def __init__(
        self,
        *,
        esb_api: ESBCachingApi,
        mprn: str,
        name: str,
        startup_delay: float = 0,
    ) -> None:
        """Initialize the sensor."""
        self._esb_api = esb_api
        self._mprn = mprn
        self._attr_name = name
        self._attr_available = True
        self._startup_delay = startup_delay
        self._setup_complete = False

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._mprn)},
            name=f"ESB Smart Meter ({self._mprn})",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @abstractmethod
    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get the data for this sensor."""

    async def async_added_to_hass(self) -> None:
        """Handle entity added to Home Assistant."""
        await super().async_added_to_hass()
        self._setup_complete = True
        
        # Schedule delayed first update if needed
        if self._startup_delay > 0:
            _LOGGER.info(
                "Scheduling delayed first update for %s in %.1f seconds",
                self._attr_name,
                self._startup_delay,
            )
            # Schedule the update to happen after the delay
            async def delayed_update():
                await asyncio.sleep(self._startup_delay)
                await self.async_update_ha_state(force_refresh=True)
            
            self.hass.async_create_task(delayed_update())

    async def async_update(self) -> None:
        """Update the sensor state."""
        try:
            esb_data = await self._esb_api.fetch()
            if esb_data:
                self._attr_native_value = self._get_data(esb_data=esb_data)
                self._attr_available = True
            else:
                self._attr_available = False
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("Network error updating %s: %s", self._attr_name, err)
            self._attr_available = False
        except (ValueError, KeyError) as err:
            _LOGGER.error("Data parsing error updating %s: %s", self._attr_name, err)
            self._attr_available = False
        except Exception as err:
            _LOGGER.error(
                "Unexpected error updating %s: %s", self._attr_name, err, exc_info=True
            )
            self._attr_available = False


class TodaySensor(BaseSensor):
    """Sensor for today's electricity usage."""

    def __init__(
        self, *, esb_api: ESBCachingApi, mprn: str, name: str, startup_delay: float = 0
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            esb_api=esb_api, mprn=mprn, name=name, startup_delay=startup_delay
        )
        self._attr_unique_id = f"{mprn}_today"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get today's data."""
        return esb_data.today


class Last24HoursSensor(BaseSensor):
    """Sensor for last 24 hours electricity usage."""

    def __init__(
        self, *, esb_api: ESBCachingApi, mprn: str, name: str, startup_delay: float = 0
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            esb_api=esb_api, mprn=mprn, name=name, startup_delay=startup_delay
        )
        self._attr_unique_id = f"{mprn}_last_24_hours"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get last 24 hours data."""
        return esb_data.last_24_hours


class ThisWeekSensor(BaseSensor):
    """Sensor for this week's electricity usage."""

    def __init__(
        self, *, esb_api: ESBCachingApi, mprn: str, name: str, startup_delay: float = 0
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            esb_api=esb_api, mprn=mprn, name=name, startup_delay=startup_delay
        )
        self._attr_unique_id = f"{mprn}_this_week"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get this week's data."""
        return esb_data.this_week


class Last7DaysSensor(BaseSensor):
    """Sensor for last 7 days electricity usage."""

    def __init__(
        self, *, esb_api: ESBCachingApi, mprn: str, name: str, startup_delay: float = 0
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            esb_api=esb_api, mprn=mprn, name=name, startup_delay=startup_delay
        )
        self._attr_unique_id = f"{mprn}_last_7_days"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get last 7 days data."""
        return esb_data.last_7_days


class ThisMonthSensor(BaseSensor):
    """Sensor for this month's electricity usage."""

    def __init__(
        self, *, esb_api: ESBCachingApi, mprn: str, name: str, startup_delay: float = 0
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            esb_api=esb_api, mprn=mprn, name=name, startup_delay=startup_delay
        )
        self._attr_unique_id = f"{mprn}_this_month"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get this month's data."""
        return esb_data.this_month


class Last30DaysSensor(BaseSensor):
    """Sensor for last 30 days electricity usage."""

    def __init__(
        self, *, esb_api: ESBCachingApi, mprn: str, name: str, startup_delay: float = 0
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            esb_api=esb_api, mprn=mprn, name=name, startup_delay=startup_delay
        )
        self._attr_unique_id = f"{mprn}_last_30_days"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get last 30 days data."""
        return esb_data.last_30_days
