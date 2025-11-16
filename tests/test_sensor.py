"""Integration tests for sensor.py with coordinator pattern."""

from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.esb_smart_meter.const import DOMAIN
from custom_components.esb_smart_meter.models import ESBData
from custom_components.esb_smart_meter.sensor import (
    ApiStatusSensor,
    DataAgeSensor,
    Last7DaysSensor,
    Last24HoursSensor,
    Last30DaysSensor,
    LastUpdateSensor,
    ThisMonthSensor,
    ThisWeekSensor,
    TodaySensor,
    async_setup_entry,
)
from tests.conftest import _async_create_task_handler


class TestAsyncSetupEntry:
    """Test async_setup_entry function with coordinator."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        coordinator = MagicMock(spec=DataUpdateCoordinator)
        coordinator.data = ESBData(
            data=[
                {
                    "Read Date and End Time": "31-12-2024 00:30",
                    "Read Value": "1.5",
                    "Read Type": "Active Import",
                    "MPRN": "12345678901",
                }
            ]
        )
        coordinator.mprn = "12345678901"
        # Mock esb_api and circuit breaker to prevent RuntimeWarnings
        coordinator.esb_api = MagicMock()
        mock_circuit_breaker = MagicMock()
        mock_circuit_breaker._is_open = False
        mock_circuit_breaker._failure_count = 0
        mock_circuit_breaker._daily_attempts = 0
        mock_circuit_breaker._last_failure_time = None
        mock_circuit_breaker.can_attempt.return_value = True
        coordinator.esb_api._circuit_breaker = mock_circuit_breaker
        # Mock hass on esb_api to prevent async task creation
        coordinator.esb_api._hass = MagicMock()
        coordinator.esb_api._hass.async_create_task = MagicMock(side_effect=_async_create_task_handler)
        return coordinator

    @pytest.fixture
    def mock_hass(self, mock_coordinator):
        """Create mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.data = {DOMAIN: {"test_entry_id": {"coordinator": mock_coordinator}}}
        # Mock async_create_task to properly close coroutines and prevent RuntimeWarnings
        hass.async_create_task = MagicMock(side_effect=_async_create_task_handler)
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create mock config entry."""
        entry = MagicMock(spec=ConfigEntry)
        entry.entry_id = "test_entry_id"
        return entry

    @pytest.mark.asyncio
    async def test_setup_entry_creates_all_sensors(self, mock_hass, mock_config_entry):
        """Test that setup_entry creates all 10 sensors (including circuit breaker)."""
        async_add_entities = MagicMock()

        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        # Verify 10 sensors were created (6 data + 4 diagnostic sensors)
        assert async_add_entities.called
        sensors = async_add_entities.call_args[0][0]
        assert len(sensors) == 10

        # Verify sensor types
        assert isinstance(sensors[0], TodaySensor)
        assert isinstance(sensors[1], Last24HoursSensor)
        assert isinstance(sensors[2], ThisWeekSensor)
        assert isinstance(sensors[3], Last7DaysSensor)
        assert isinstance(sensors[4], ThisMonthSensor)
        assert isinstance(sensors[5], Last30DaysSensor)
        # Diagnostic sensors
        assert isinstance(sensors[6], LastUpdateSensor)
        assert isinstance(sensors[7], ApiStatusSensor)
        assert isinstance(sensors[8], DataAgeSensor)
        assert isinstance(sensors[4], ThisMonthSensor)
        assert isinstance(sensors[5], Last30DaysSensor)


class TestBaseSensor:
    """Test BaseSensor class with coordinator."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        coordinator = MagicMock(spec=DataUpdateCoordinator)
        coordinator.data = ESBData(
            data=[
                {
                    "Read Date and End Time": "31-12-2024 00:30",
                    "Read Value": "1.5",
                    "Read Type": "Active Import",
                    "MPRN": "12345678901",
                }
            ]
        )
        return coordinator

    def test_sensor_reads_from_coordinator(self, mock_coordinator):
        """Test sensor reads data from coordinator."""
        sensor = TodaySensor(coordinator=mock_coordinator, mprn="12345678901")

        value = sensor.native_value

        assert value == mock_coordinator.data.today

    def test_sensor_handles_no_data(self):
        """Test sensor handles when coordinator has no data."""
        coordinator = MagicMock(spec=DataUpdateCoordinator)
        coordinator.data = None

        sensor = TodaySensor(coordinator=coordinator, mprn="12345678901")

        value = sensor.native_value

        assert value is None

    def test_sensor_device_info(self, mock_coordinator):
        """Test sensor device info."""
        sensor = TodaySensor(coordinator=mock_coordinator, mprn="12345678901")

        device_info = sensor.device_info

        assert device_info["identifiers"] == {(DOMAIN, "12345678901")}
        assert "ESB Smart Meter" in device_info["name"]
        assert "12345678901" in device_info["name"]

    def test_sensor_unit_of_measurement(self, mock_coordinator):
        """Test sensor has correct unit of measurement."""
        sensor = TodaySensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor._attr_native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR

    def test_sensor_icon(self, mock_coordinator):
        """Test sensor has correct icon."""
        sensor = TodaySensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor._attr_icon == "mdi:flash"


class TestTodaySensor:
    """Test TodaySensor class."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        return MagicMock(spec=DataUpdateCoordinator)

    def test_unique_id(self, mock_coordinator):
        """Test Today sensor unique ID."""
        sensor = TodaySensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor._attr_unique_id == "12345678901_today"

    def test_get_data(self, mock_coordinator):
        """Test Today sensor gets correct data."""
        sensor = TodaySensor(coordinator=mock_coordinator, mprn="12345678901")

        esb_data = MagicMock()
        esb_data.today = 15.5

        result = sensor._get_data(esb_data=esb_data)
        assert result == 15.5


class TestLast24HoursSensor:
    """Test Last24HoursSensor class."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        return MagicMock(spec=DataUpdateCoordinator)

    def test_unique_id(self, mock_coordinator):
        """Test Last 24 Hours sensor unique ID."""
        sensor = Last24HoursSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor._attr_unique_id == "12345678901_last_24_hours"

    def test_get_data(self, mock_coordinator):
        """Test Last 24 Hours sensor gets correct data."""
        sensor = Last24HoursSensor(coordinator=mock_coordinator, mprn="12345678901")

        esb_data = MagicMock()
        esb_data.last_24_hours = 25.3

        result = sensor._get_data(esb_data=esb_data)
        assert result == 25.3


class TestThisWeekSensor:
    """Test ThisWeekSensor class."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        return MagicMock(spec=DataUpdateCoordinator)

    def test_unique_id(self, mock_coordinator):
        """Test This Week sensor unique ID."""
        sensor = ThisWeekSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor._attr_unique_id == "12345678901_this_week"

    def test_get_data(self, mock_coordinator):
        """Test This Week sensor gets correct data."""
        sensor = ThisWeekSensor(coordinator=mock_coordinator, mprn="12345678901")

        esb_data = MagicMock()
        esb_data.this_week = 85.7

        result = sensor._get_data(esb_data=esb_data)
        assert result == 85.7


class TestLast7DaysSensor:
    """Test Last7DaysSensor class."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        return MagicMock(spec=DataUpdateCoordinator)

    def test_unique_id(self, mock_coordinator):
        """Test Last 7 Days sensor unique ID."""
        sensor = Last7DaysSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor._attr_unique_id == "12345678901_last_7_days"

    def test_get_data(self, mock_coordinator):
        """Test Last 7 Days sensor gets correct data."""
        sensor = Last7DaysSensor(coordinator=mock_coordinator, mprn="12345678901")

        esb_data = MagicMock()
        esb_data.last_7_days = 175.2

        result = sensor._get_data(esb_data=esb_data)
        assert result == 175.2


class TestThisMonthSensor:
    """Test ThisMonthSensor class."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        return MagicMock(spec=DataUpdateCoordinator)

    def test_unique_id(self, mock_coordinator):
        """Test This Month sensor unique ID."""
        sensor = ThisMonthSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor._attr_unique_id == "12345678901_this_month"

    def test_get_data(self, mock_coordinator):
        """Test This Month sensor gets correct data."""
        sensor = ThisMonthSensor(coordinator=mock_coordinator, mprn="12345678901")

        esb_data = MagicMock()
        esb_data.this_month = 450.8

        result = sensor._get_data(esb_data=esb_data)
        assert result == 450.8


class TestLast30DaysSensor:
    """Test Last30DaysSensor class."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        return MagicMock(spec=DataUpdateCoordinator)

    def test_unique_id(self, mock_coordinator):
        """Test Last 30 Days sensor unique ID."""
        sensor = Last30DaysSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor._attr_unique_id == "12345678901_last_30_days"

    def test_get_data(self, mock_coordinator):
        """Test Last 30 Days sensor gets correct data."""
        sensor = Last30DaysSensor(coordinator=mock_coordinator, mprn="12345678901")

        esb_data = MagicMock()
        esb_data.last_30_days = 520.6

        result = sensor._get_data(esb_data=esb_data)
        assert result == 520.6


class TestLastUpdateSensor:
    """Test LastUpdateSensor class."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        return MagicMock(spec=DataUpdateCoordinator)

    def test_unique_id(self, mock_coordinator):
        """Test Last Update sensor unique ID."""
        sensor = LastUpdateSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor._attr_unique_id == "12345678901_last_update"

    def test_native_value_none(self, mock_coordinator):
        """Test Last Update sensor returns None when last_successful_update_time is None."""
        mock_coordinator.last_successful_update_time = None
        sensor = LastUpdateSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor.native_value is None

    def test_native_value_datetime(self, mock_coordinator):
        """Test Last Update sensor returns isoformat when last_successful_update_time is datetime."""
        from datetime import datetime, timezone

        test_time = datetime(2024, 12, 31, 12, 30, 0, tzinfo=timezone.utc)
        mock_coordinator.last_successful_update_time = test_time
        sensor = LastUpdateSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor.native_value == test_time.isoformat()


class TestApiStatusSensor:
    """Test ApiStatusSensor class."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        return MagicMock(spec=DataUpdateCoordinator)

    def test_unique_id(self, mock_coordinator):
        """Test API Status sensor unique ID."""
        sensor = ApiStatusSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor._attr_unique_id == "12345678901_api_status"

    def test_native_value_unknown_when_none(self, mock_coordinator):
        """Test API Status sensor returns unknown when last_update_success is None."""
        mock_coordinator.last_update_success = None
        sensor = ApiStatusSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor.native_value == "unknown"

    def test_native_value_error_when_no_data(self, mock_coordinator):
        """Test API Status sensor returns error when coordinator has no data."""
        from datetime import datetime, timezone

        mock_coordinator.last_update_success = datetime.now(timezone.utc)
        mock_coordinator.data = None
        sensor = ApiStatusSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor.native_value == "error"

    def test_native_value_online_when_has_data(self, mock_coordinator):
        """Test API Status sensor returns online when coordinator has data."""
        from datetime import datetime, timezone

        mock_coordinator.last_update_success = datetime.now(timezone.utc)
        mock_coordinator.data = MagicMock()
        sensor = ApiStatusSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor.native_value == "online"


class TestDataAgeSensor:
    """Test DataAgeSensor class."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        return MagicMock(spec=DataUpdateCoordinator)

    def test_unique_id(self, mock_coordinator):
        """Test Data Age sensor unique ID."""
        sensor = DataAgeSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor._attr_unique_id == "12345678901_data_age"

    def test_native_value_none(self, mock_coordinator):
        """Test Data Age sensor returns None when last_successful_update_time is None."""
        mock_coordinator.last_successful_update_time = None
        sensor = DataAgeSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor.native_value is None

    def test_native_value_calculates_age(self, mock_coordinator):
        """Test Data Age sensor calculates age correctly."""
        from datetime import datetime, timedelta, timezone

        # Set last update to 2 hours ago
        test_time = datetime.now(timezone.utc) - timedelta(hours=2)
        mock_coordinator.last_successful_update_time = test_time
        sensor = DataAgeSensor(coordinator=mock_coordinator, mprn="12345678901")

        age = sensor.native_value
        assert age is not None
        # Should be approximately 2 hours
        assert 1.9 < age < 2.1

    def test_native_unit_of_measurement(self, mock_coordinator):
        """Test Data Age sensor has correct unit."""
        sensor = DataAgeSensor(coordinator=mock_coordinator, mprn="12345678901")

        from homeassistant.const import UnitOfTime

        assert sensor._attr_native_unit_of_measurement == UnitOfTime.HOURS
