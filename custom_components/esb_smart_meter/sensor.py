"""Support for ESB Smart Meter sensors."""

import logging
from abc import abstractmethod

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import ESBDataUpdateCoordinator
from .models import ESBData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ESB Smart Meter sensor based on a config entry."""
    # Get coordinator from hass.data
    coordinator: ESBDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    mprn = coordinator.mprn

    # Create all sensors using the coordinator
    sensors = [
        TodaySensor(coordinator=coordinator, mprn=mprn),
        Last24HoursSensor(coordinator=coordinator, mprn=mprn),
        ThisWeekSensor(coordinator=coordinator, mprn=mprn),
        Last7DaysSensor(coordinator=coordinator, mprn=mprn),
        ThisMonthSensor(coordinator=coordinator, mprn=mprn),
        Last30DaysSensor(coordinator=coordinator, mprn=mprn),
    ]

    # Add entities - coordinator handles updates
    async_add_entities(sensors)


class BaseSensor(CoordinatorEntity[ESBDataUpdateCoordinator], SensorEntity):
    """Base sensor class for ESB Smart Meter sensors using coordinator."""

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_icon = "mdi:flash"

    def __init__(
        self,
        *,
        coordinator: ESBDataUpdateCoordinator,
        mprn: str,
        name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._mprn = mprn
        self._attr_name = name

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
        """Get the data for this sensor from coordinator data."""

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        return self._get_data(esb_data=self.coordinator.data)


class TodaySensor(BaseSensor):
    """Sensor for today's electricity usage."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator=coordinator,
            mprn=mprn,
            name="ESB Electricity Usage: Today",
        )
        self._attr_unique_id = f"{mprn}_today"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get today's data."""
        return esb_data.today


class Last24HoursSensor(BaseSensor):
    """Sensor for last 24 hours electricity usage."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator=coordinator,
            mprn=mprn,
            name="ESB Electricity Usage: Last 24 Hours",
        )
        self._attr_unique_id = f"{mprn}_last_24_hours"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get last 24 hours data."""
        return esb_data.last_24_hours


class ThisWeekSensor(BaseSensor):
    """Sensor for this week's electricity usage."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator=coordinator,
            mprn=mprn,
            name="ESB Electricity Usage: This Week",
        )
        self._attr_unique_id = f"{mprn}_this_week"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get this week's data."""
        return esb_data.this_week


class Last7DaysSensor(BaseSensor):
    """Sensor for last 7 days electricity usage."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator=coordinator,
            mprn=mprn,
            name="ESB Electricity Usage: Last 7 Days",
        )
        self._attr_unique_id = f"{mprn}_last_7_days"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get last 7 days data."""
        return esb_data.last_7_days


class ThisMonthSensor(BaseSensor):
    """Sensor for this month's electricity usage."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator=coordinator,
            mprn=mprn,
            name="ESB Electricity Usage: This Month",
        )
        self._attr_unique_id = f"{mprn}_this_month"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get this month's data."""
        return esb_data.this_month


class Last30DaysSensor(BaseSensor):
    """Sensor for last 30 days electricity usage."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator=coordinator,
            mprn=mprn,
            name="ESB Electricity Usage: Last 30 Days",
        )
        self._attr_unique_id = f"{mprn}_last_30_days"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get last 30 days data."""
        return esb_data.last_30_days
