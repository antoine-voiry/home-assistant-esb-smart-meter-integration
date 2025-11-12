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
    Last24HoursSensor,
    Last30DaysSensor,
    Last7DaysSensor,
    LastUpdateSensor,
    ThisMonthSensor,
    ThisWeekSensor,
    TodaySensor,
    async_setup_entry,
)


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
        return coordinator

    @pytest.fixture
    def mock_hass(self, mock_coordinator):
        """Create mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.data = {
            DOMAIN: {
                "test_entry_id": {"coordinator": mock_coordinator}
            }
        }
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create mock config entry."""
        entry = MagicMock(spec=ConfigEntry)
        entry.entry_id = "test_entry_id"
        return entry

    @pytest.mark.asyncio
    async def test_setup_entry_creates_all_sensors(
        self, mock_hass, mock_config_entry
    ):
        """Test that setup_entry creates all 9 sensors."""
        async_add_entities = MagicMock()

        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        # Verify 9 sensors were created
        assert async_add_entities.called
        sensors = async_add_entities.call_args[0][0]
        assert len(sensors) == 9

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
