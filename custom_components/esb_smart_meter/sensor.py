"""Support for ESB Smart Meter sensors."""

import logging
from abc import abstractmethod
from typing import Any
from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import ESBDataUpdateCoordinator
from .models import ESBData
from .statistics import async_import_hourly_statistics

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ESB Smart Meter sensor based on a config entry."""
    # Get coordinator from hass.data
    coordinator: ESBDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    mprn = coordinator.mprn

    # History sensors carry the long-term statistics; the Total sensors mirror
    # their running total for display on the device page.
    usage_history = UsageHistorySensor(coordinator=coordinator, mprn=mprn)
    export_history = ExportHistorySensor(coordinator=coordinator, mprn=mprn)

    # Create all sensors using the coordinator
    sensors = [
        TodaySensor(coordinator=coordinator, mprn=mprn),
        Last24HoursSensor(coordinator=coordinator, mprn=mprn),
        ThisWeekSensor(coordinator=coordinator, mprn=mprn),
        Last7DaysSensor(coordinator=coordinator, mprn=mprn),
        ThisMonthSensor(coordinator=coordinator, mprn=mprn),
        Last30DaysSensor(coordinator=coordinator, mprn=mprn),
        ExportTodaySensor(coordinator=coordinator, mprn=mprn),
        ExportLast24HoursSensor(coordinator=coordinator, mprn=mprn),
        ExportThisWeekSensor(coordinator=coordinator, mprn=mprn),
        ExportLast7DaysSensor(coordinator=coordinator, mprn=mprn),
        ExportThisMonthSensor(coordinator=coordinator, mprn=mprn),
        ExportLast30DaysSensor(coordinator=coordinator, mprn=mprn),
        # History (imported statistics) and their display totals
        usage_history,
        export_history,
        UsageTotalSensor(coordinator=coordinator, mprn=mprn, history=usage_history),
        ExportTotalSensor(coordinator=coordinator, mprn=mprn, history=export_history),
        # Diagnostic sensors
        LastUpdateSensor(coordinator=coordinator, mprn=mprn),
        ApiStatusSensor(coordinator=coordinator, mprn=mprn),
        DataAgeSensor(coordinator=coordinator, mprn=mprn),
        CircuitBreakerStatusSensor(coordinator=coordinator, mprn=mprn),
    ]

    # Add entities - coordinator handles updates
    async_add_entities(sensors)


class BaseSensor(CoordinatorEntity[ESBDataUpdateCoordinator], SensorEntity):
    """Base sensor class for ESB Smart Meter sensors using coordinator."""

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    # Use TOTAL (not TOTAL_INCREASING) since values are recalculated from ESB CSV
    # which may have varying historical data availability
    _attr_state_class = SensorStateClass.TOTAL
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

    @abstractmethod
    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get the readings for this sensor from coordinator data."""

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        return self._get_data(esb_data=self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the state attributes."""
        if self.coordinator.data is None:
            return None
        return {
            "readings": self._get_readings(esb_data=self.coordinator.data)
        }


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

    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get today's readings."""
        return esb_data.get_readings_since(since=esb_data.start_of_today())


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

    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get last 24 hours readings."""
        return esb_data.get_readings_since(since=esb_data.since_24_hours())


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

    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get this week's readings."""
        return esb_data.get_readings_since(since=esb_data.start_of_week())


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

    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get last 7 days readings."""
        return esb_data.get_readings_since(since=esb_data.since_7_days())


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

    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get this month's readings."""
        return esb_data.get_readings_since(since=esb_data.start_of_month())


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

    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get last 30 days readings."""
        return esb_data.get_readings_since(since=esb_data.since_30_days())


class ExportTodaySensor(BaseSensor):
    """Sensor for today's grid export."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator=coordinator, mprn=mprn, name="ESB Electricity Export: Today")
        self._attr_unique_id = f"{mprn}_export_today"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get today's export."""
        return esb_data.export_today

    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get today's export readings."""
        return esb_data.get_export_readings_since(since=esb_data.start_of_today())


class ExportLast24HoursSensor(BaseSensor):
    """Sensor for last 24 hours grid export."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator=coordinator, mprn=mprn, name="ESB Electricity Export: Last 24 Hours")
        self._attr_unique_id = f"{mprn}_export_last_24_hours"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get last 24 hours export."""
        return esb_data.export_last_24_hours

    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get last 24 hours export readings."""
        return esb_data.get_export_readings_since(since=esb_data.since_24_hours())


class ExportThisWeekSensor(BaseSensor):
    """Sensor for this week's grid export."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator=coordinator, mprn=mprn, name="ESB Electricity Export: This Week")
        self._attr_unique_id = f"{mprn}_export_this_week"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get this week's export."""
        return esb_data.export_this_week

    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get this week's export readings."""
        return esb_data.get_export_readings_since(since=esb_data.start_of_week())


class ExportLast7DaysSensor(BaseSensor):
    """Sensor for last 7 days grid export."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator=coordinator, mprn=mprn, name="ESB Electricity Export: Last 7 Days")
        self._attr_unique_id = f"{mprn}_export_last_7_days"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get last 7 days export."""
        return esb_data.export_last_7_days

    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get last 7 days export readings."""
        return esb_data.get_export_readings_since(since=esb_data.since_7_days())


class ExportThisMonthSensor(BaseSensor):
    """Sensor for this month's grid export."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator=coordinator, mprn=mprn, name="ESB Electricity Export: This Month")
        self._attr_unique_id = f"{mprn}_export_this_month"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get this month's export."""
        return esb_data.export_this_month

    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get this month's export readings."""
        return esb_data.get_export_readings_since(since=esb_data.start_of_month())


class ExportLast30DaysSensor(BaseSensor):
    """Sensor for last 30 days grid export."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator=coordinator, mprn=mprn, name="ESB Electricity Export: Last 30 Days")
        self._attr_unique_id = f"{mprn}_export_last_30_days"

    def _get_data(self, *, esb_data: ESBData) -> float:
        """Get last 30 days export."""
        return esb_data.export_last_30_days

    def _get_readings(self, *, esb_data: ESBData) -> list[dict[str, Any]]:
        """Get last 30 days export readings."""
        return esb_data.get_export_readings_since(since=esb_data.since_30_days())


class BaseHistorySensor(CoordinatorEntity[ESBDataUpdateCoordinator], SensorEntity):
    """Backfills hourly history into long-term statistics.

    Imports hourly history against its own statistic id so the statistics stay
    grouped under the device. The entity is stateless (see native_value); the
    paired Total sensor mirrors the running cumulative total for display.
    """

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_icon = "mdi:flash"

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str, name: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._mprn = mprn
        self._attr_name = name
        self._cumulative: float | None = None
        self._total_sensor: "BaseTotalSensor | None" = None

    def register_total(self, total: "BaseTotalSensor") -> None:
        """Link the display Total sensor so it can be refreshed after imports."""
        self._total_sensor = total

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
    def _hourly(self, *, esb_data: ESBData) -> list[tuple[Any, float]]:
        """Return the hourly (hour_start, kWh) buckets for this series."""

    @property
    def native_value(self) -> None:
        """Return no state; the data lives in the imported statistics.

        A state_class would make the recorder auto-compile a second, conflicting
        statistics series from the live state, so this stays stateless.
        """
        return None

    async def async_added_to_hass(self) -> None:
        """Import statistics once the entity (and its id) exists."""
        await super().async_added_to_hass()
        await self._async_import_statistics()

    def _handle_coordinator_update(self) -> None:
        """Import new statistics on each refresh."""
        self.hass.async_create_task(self._async_import_statistics())
        super()._handle_coordinator_update()

    async def _async_import_statistics(self) -> None:
        """Import hourly buckets and refresh the paired Total sensor."""
        if self.coordinator.data is None:
            return
        total = await async_import_hourly_statistics(
            self.hass, self.entity_id, self._hourly(esb_data=self.coordinator.data)
        )
        if total is not None:
            self._cumulative = total
            if self._total_sensor is not None:
                self._total_sensor.async_write_total()


class UsageHistorySensor(BaseHistorySensor):
    """Consumption history backfilled into long-term statistics."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator=coordinator, mprn=mprn, name="ESB Electricity Usage: History")
        self._attr_unique_id = f"{mprn}_usage_history"

    def _hourly(self, *, esb_data: ESBData) -> list[tuple[Any, float]]:
        """Get hourly usage buckets."""
        return esb_data.hourly_usage()


class ExportHistorySensor(BaseHistorySensor):
    """Grid export history backfilled into long-term statistics."""

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator=coordinator, mprn=mprn, name="ESB Electricity Export: History")
        self._attr_unique_id = f"{mprn}_export_history"

    def _hourly(self, *, esb_data: ESBData) -> list[tuple[Any, float]]:
        """Get hourly export buckets."""
        return esb_data.hourly_export()


class BaseTotalSensor(CoordinatorEntity[ESBDataUpdateCoordinator], SensorEntity):
    """Display-only cumulative total mirroring its paired History sensor.

    Has no state_class, so the recorder never auto-compiles statistics for it;
    the History sensor carries the long-term statistics. This entity just shows
    the running total on the device page.
    """

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_icon = "mdi:flash"

    def __init__(
        self,
        *,
        coordinator: ESBDataUpdateCoordinator,
        mprn: str,
        name: str,
        history: BaseHistorySensor,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._mprn = mprn
        self._attr_name = name
        self._history = history
        history.register_total(self)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._mprn)},
            name=f"ESB Smart Meter ({self._mprn})",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def native_value(self) -> float | None:
        """Return the running cumulative total from the paired History sensor."""
        return self._history._cumulative

    def async_write_total(self) -> None:
        """Write the latest total to the state machine (called by the History sensor)."""
        if self.hass is not None:
            self.async_write_ha_state()


class UsageTotalSensor(BaseTotalSensor):
    """Cumulative consumption total (display)."""

    def __init__(
        self, *, coordinator: ESBDataUpdateCoordinator, mprn: str, history: BaseHistorySensor
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator=coordinator, mprn=mprn, name="ESB Electricity Usage: Total", history=history
        )
        self._attr_unique_id = f"{mprn}_usage_total"


class ExportTotalSensor(BaseTotalSensor):
    """Cumulative grid export total (display)."""

    def __init__(
        self, *, coordinator: ESBDataUpdateCoordinator, mprn: str, history: BaseHistorySensor
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator=coordinator, mprn=mprn, name="ESB Electricity Export: Total", history=history
        )
        self._attr_unique_id = f"{mprn}_export_total"


class LastUpdateSensor(SensorEntity):
    """Sensor for last update timestamp."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_state_class = None  # Timestamps don't use state class
    _attr_native_unit_of_measurement = None
    _attr_icon = "mdi:clock-outline"

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__()
        self.coordinator = coordinator
        self._mprn = mprn
        self._attr_name = "ESB Smart Meter: Last Update"
        self._attr_unique_id = f"{mprn}_last_update"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._mprn)},
            name=f"ESB Smart Meter ({self._mprn})",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        """Return the timestamp of the last successful update."""
        return self.coordinator.last_successful_update_time


class ApiStatusSensor(SensorEntity):
    """Sensor for API status."""

    _attr_device_class = None
    _attr_state_class = None
    _attr_native_unit_of_measurement = None
    _attr_icon = "mdi:api"

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__()
        self.coordinator = coordinator
        self._mprn = mprn
        self._attr_name = "ESB Smart Meter: API Status"
        self._attr_unique_id = f"{mprn}_api_status"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._mprn)},
            name=f"ESB Smart Meter ({self._mprn})",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        """Return the API status."""
        if self.coordinator.last_update_success is None:
            return "unknown"
        if self.coordinator.data is None:
            return "error"
        return "online"


class DataAgeSensor(SensorEntity):
    """Sensor for data age in hours."""

    _attr_device_class = None
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "h"
    _attr_icon = "mdi:timer-outline"

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__()
        self.coordinator = coordinator
        self._mprn = mprn
        self._attr_name = "ESB Smart Meter: Data Age"
        self._attr_unique_id = f"{mprn}_data_age"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._mprn)},
            name=f"ESB Smart Meter ({self._mprn})",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        """Return the age of the data in hours."""
        if self.coordinator.last_successful_update_time is None:
            return None

        age = datetime.now(timezone.utc) - self.coordinator.last_successful_update_time
        return round(age.total_seconds() / 3600, 1)  # Hours with 1 decimal place


class CircuitBreakerStatusSensor(SensorEntity):
    """Sensor showing circuit breaker state and health."""

    _attr_device_class = None
    _attr_state_class = None
    _attr_native_unit_of_measurement = None
    _attr_icon = "mdi:electric-switch"

    def __init__(self, *, coordinator: ESBDataUpdateCoordinator, mprn: str) -> None:
        """Initialize the sensor."""
        super().__init__()
        self.coordinator = coordinator
        self._mprn = mprn
        self._attr_name = "ESB Smart Meter: Circuit Breaker Status"
        self._attr_unique_id = f"{mprn}_circuit_breaker_status"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._mprn)},
            name=f"ESB Smart Meter ({self._mprn})",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        """Return circuit breaker state."""
        cb = self.coordinator.esb_api._circuit_breaker
        
        if not hasattr(cb, '_is_open'):
            return "unknown"
        
        now = datetime.now()
        
        # Check if circuit is open
        if cb._is_open and cb._last_failure_time:
            # Calculate backoff time
            from .const import CIRCUIT_BREAKER_TIMEOUT, CIRCUIT_BREAKER_MAX_TIMEOUT
            backoff_time = min(
                CIRCUIT_BREAKER_TIMEOUT * (2 ** (cb._failure_count - 1)),
                CIRCUIT_BREAKER_MAX_TIMEOUT,
            )
            elapsed = (now - cb._last_failure_time).total_seconds()
            
            if elapsed < backoff_time:
                return "open"
            return "half_open"
        
        return "closed"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        cb = self.coordinator.esb_api._circuit_breaker
        
        if not hasattr(cb, '_failure_count'):
            return {}
        
        attrs = {
            "failure_count": cb._failure_count,
            "daily_attempts": cb._daily_attempts,
        }
        
        # Add constants for reference
        from .const import (
            CIRCUIT_BREAKER_FAILURES,
            CIRCUIT_BREAKER_MAX_TIMEOUT,
            MAX_AUTH_ATTEMPTS_PER_DAY,
        )
        attrs["failure_threshold"] = CIRCUIT_BREAKER_FAILURES
        attrs["daily_limit"] = MAX_AUTH_ATTEMPTS_PER_DAY
        
        # Add backoff information if circuit is open
        if cb._is_open and cb._last_failure_time:
            now = datetime.now()
            from .const import CIRCUIT_BREAKER_TIMEOUT
            backoff_time = min(
                CIRCUIT_BREAKER_TIMEOUT * (2 ** (cb._failure_count - 1)),
                CIRCUIT_BREAKER_MAX_TIMEOUT,
            )
            elapsed = (now - cb._last_failure_time).total_seconds()
            remaining = max(0, backoff_time - elapsed)
            
            attrs["backoff_seconds"] = int(backoff_time)
            attrs["time_remaining_seconds"] = int(remaining)
            attrs["time_remaining_minutes"] = round(remaining / 60, 1)
            
            if remaining > 0:
                blocked_until = cb._last_failure_time
                from datetime import timedelta
                blocked_until = blocked_until + timedelta(seconds=backoff_time)
                attrs["blocked_until"] = blocked_until.isoformat()
        
        if cb._last_failure_time:
            attrs["last_failure"] = cb._last_failure_time.isoformat()
        
        if cb._daily_attempts_reset_time:
            attrs["daily_counter_resets"] = cb._daily_attempts_reset_time.date().isoformat()
        
        return attrs

    @property
    def icon(self) -> str:
        """Return icon based on circuit breaker state."""
        state = self.native_value
        return {
            "closed": "mdi:check-circle",
            "open": "mdi:alert-circle",
            "half_open": "mdi:refresh-circle",
            "unknown": "mdi:help-circle",
        }.get(state, "mdi:help-circle")
