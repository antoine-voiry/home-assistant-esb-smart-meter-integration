"""Integration tests for sensor.py."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant

from custom_components.esb_smart_meter.const import (
    CONF_MPRN,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.esb_smart_meter.models import ESBData
from custom_components.esb_smart_meter.sensor import (
    Last24HoursSensor,
    Last30DaysSensor,
    Last7DaysSensor,
    ThisMonthSensor,
    ThisWeekSensor,
    TodaySensor,
    async_setup_entry,
)


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.data = {DOMAIN: {}}
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create mock config entry."""
        entry = MagicMock(spec=ConfigEntry)
        entry.data = {
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "password123",
            CONF_MPRN: "12345678901",
        }
        entry.entry_id = "test_entry_id"
        return entry

    @pytest.mark.asyncio
    async def test_setup_entry_creates_all_sensors(
        self, mock_hass, mock_config_entry
    ):
        """Test that setup_entry creates all 6 sensors."""
        async_add_entities = MagicMock()

        # Mock session creation
        mock_session = MagicMock()
        with patch(
            "custom_components.esb_smart_meter.sensor.create_esb_session",
            return_value=mock_session,
        ):
            with patch(
                "custom_components.esb_smart_meter.sensor.get_startup_delay",
                return_value=0,
            ):
                await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        # Verify 6 sensors were created
        assert async_add_entities.called
        sensors = async_add_entities.call_args[0][0]
        assert len(sensors) == 6

        # Verify sensor types
        assert isinstance(sensors[0], TodaySensor)
        assert isinstance(sensors[1], Last24HoursSensor)
        assert isinstance(sensors[2], ThisWeekSensor)
        assert isinstance(sensors[3], Last7DaysSensor)
        assert isinstance(sensors[4], ThisMonthSensor)
        assert isinstance(sensors[5], Last30DaysSensor)

    @pytest.mark.asyncio
    async def test_setup_entry_stores_session(self, mock_hass, mock_config_entry):
        """Test that session is stored in hass.data for cleanup."""
        async_add_entities = MagicMock()
        mock_session = MagicMock()

        # Initialize the entry data
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {}

        with patch(
            "custom_components.esb_smart_meter.sensor.create_esb_session",
            return_value=mock_session,
        ):
            with patch(
                "custom_components.esb_smart_meter.sensor.get_startup_delay",
                return_value=0,
            ):
                await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        # Verify session stored
        assert "session" in mock_hass.data[DOMAIN][mock_config_entry.entry_id]
        assert (
            mock_hass.data[DOMAIN][mock_config_entry.entry_id]["session"]
            == mock_session
        )

    @pytest.mark.asyncio
    async def test_setup_entry_applies_startup_delay(
        self, mock_hass, mock_config_entry
    ):
        """Test that startup delay is calculated and passed to sensors."""
        async_add_entities = MagicMock()
        startup_delay = 15.5

        with patch(
            "custom_components.esb_smart_meter.sensor.create_esb_session",
            return_value=MagicMock(),
        ):
            with patch(
                "custom_components.esb_smart_meter.sensor.get_startup_delay",
                return_value=startup_delay,
            ):
                await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        # Verify all sensors have startup delay
        sensors = async_add_entities.call_args[0][0]
        for sensor in sensors:
            assert sensor._startup_delay == startup_delay


class TestBaseSensor:
    """Test BaseSensor class through concrete implementations."""

    @pytest.fixture
    def mock_esb_api(self):
        """Create mock ESB API."""
        api = MagicMock()
        api.fetch = AsyncMock()
        return api

    @pytest.fixture
    def sample_esb_data(self):
        """Create sample ESB data."""
        return ESBData(
            csv_data=[
                {
                    "Read Date and End Time": "31-12-2024 00:30",
                    "Read Value": "1.5",
                    "Read Type": "Active Import",
                    "MPRN": "12345678901",
                }
            ]
        )

    @pytest.mark.asyncio
    async def test_sensor_successful_update(self, mock_esb_api, sample_esb_data):
        """Test successful sensor update."""
        mock_esb_api.fetch.return_value = sample_esb_data

        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test Sensor",
            startup_delay=0,
        )

        await sensor.async_update()

        assert sensor._attr_available is True
        assert sensor._attr_native_value == sample_esb_data.today

    @pytest.mark.asyncio
    async def test_sensor_update_with_startup_delay(
        self, mock_esb_api, sample_esb_data
    ):
        """Test sensor applies startup delay on first update."""
        mock_esb_api.fetch.return_value = sample_esb_data

        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test Sensor",
            startup_delay=0.1,  # Short delay for testing
        )

        # First update should include delay
        await sensor.async_update()
        assert sensor._first_update is False

        # Second update should not delay
        mock_esb_api.fetch.reset_mock()
        await sensor.async_update()
        assert mock_esb_api.fetch.called

    @pytest.mark.asyncio
    async def test_sensor_update_no_data(self, mock_esb_api):
        """Test sensor handles no data returned."""
        mock_esb_api.fetch.return_value = None

        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test Sensor",
            startup_delay=0,
        )

        await sensor.async_update()

        assert sensor._attr_available is False

    @pytest.mark.asyncio
    async def test_sensor_update_network_error(self, mock_esb_api):
        """ERROR PATH: Test sensor handles network errors."""
        mock_esb_api.fetch.side_effect = aiohttp.ClientError("Network error")

        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test Sensor",
            startup_delay=0,
        )

        await sensor.async_update()

        assert sensor._attr_available is False

    @pytest.mark.asyncio
    async def test_sensor_update_timeout_error(self, mock_esb_api):
        """ERROR PATH: Test sensor handles timeout errors."""
        mock_esb_api.fetch.side_effect = asyncio.TimeoutError()

        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test Sensor",
            startup_delay=0,
        )

        await sensor.async_update()

        assert sensor._attr_available is False

    @pytest.mark.asyncio
    async def test_sensor_update_value_error(self, mock_esb_api):
        """ERROR PATH: Test sensor handles data parsing errors."""
        mock_esb_api.fetch.side_effect = ValueError("Invalid data format")

        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test Sensor",
            startup_delay=0,
        )

        await sensor.async_update()

        assert sensor._attr_available is False

    @pytest.mark.asyncio
    async def test_sensor_update_key_error(self, mock_esb_api):
        """ERROR PATH: Test sensor handles missing key errors."""
        mock_esb_api.fetch.side_effect = KeyError("missing_field")

        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test Sensor",
            startup_delay=0,
        )

        await sensor.async_update()

        assert sensor._attr_available is False

    @pytest.mark.asyncio
    async def test_sensor_update_unexpected_error(self, mock_esb_api):
        """ERROR PATH: Test sensor handles unexpected errors."""
        mock_esb_api.fetch.side_effect = RuntimeError("Unexpected error")

        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test Sensor",
            startup_delay=0,
        )

        await sensor.async_update()

        assert sensor._attr_available is False

    def test_sensor_device_info(self, mock_esb_api):
        """Test sensor device info."""
        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test Sensor",
            startup_delay=0,
        )

        device_info = sensor.device_info

        assert device_info["identifiers"] == {(DOMAIN, "12345678901")}
        assert "ESB Smart Meter" in device_info["name"]
        assert "12345678901" in device_info["name"]

    def test_sensor_unit_of_measurement(self, mock_esb_api):
        """Test sensor has correct unit of measurement."""
        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test Sensor",
            startup_delay=0,
        )

        assert sensor._attr_native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR

    def test_sensor_icon(self, mock_esb_api):
        """Test sensor has correct icon."""
        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test Sensor",
            startup_delay=0,
        )

        assert sensor._attr_icon == "mdi:flash"


class TestTodaySensor:
    """Test TodaySensor class."""

    @pytest.fixture
    def mock_esb_api(self):
        """Create mock ESB API."""
        return MagicMock()

    def test_unique_id(self, mock_esb_api):
        """Test Today sensor unique ID."""
        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test",
            startup_delay=0,
        )

        assert sensor._attr_unique_id == "12345678901_today"

    def test_get_data(self, mock_esb_api):
        """Test Today sensor gets correct data."""
        sensor = TodaySensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test",
            startup_delay=0,
        )

        esb_data = MagicMock()
        esb_data.today = 15.5

        result = sensor._get_data(esb_data=esb_data)
        assert result == 15.5


class TestLast24HoursSensor:
    """Test Last24HoursSensor class."""

    @pytest.fixture
    def mock_esb_api(self):
        """Create mock ESB API."""
        return MagicMock()

    def test_unique_id(self, mock_esb_api):
        """Test Last 24 Hours sensor unique ID."""
        sensor = Last24HoursSensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test",
            startup_delay=0,
        )

        assert sensor._attr_unique_id == "12345678901_last_24_hours"

    def test_get_data(self, mock_esb_api):
        """Test Last 24 Hours sensor gets correct data."""
        sensor = Last24HoursSensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test",
            startup_delay=0,
        )

        esb_data = MagicMock()
        esb_data.last_24_hours = 25.3

        result = sensor._get_data(esb_data=esb_data)
        assert result == 25.3


class TestThisWeekSensor:
    """Test ThisWeekSensor class."""

    @pytest.fixture
    def mock_esb_api(self):
        """Create mock ESB API."""
        return MagicMock()

    def test_unique_id(self, mock_esb_api):
        """Test This Week sensor unique ID."""
        sensor = ThisWeekSensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test",
            startup_delay=0,
        )

        assert sensor._attr_unique_id == "12345678901_this_week"

    def test_get_data(self, mock_esb_api):
        """Test This Week sensor gets correct data."""
        sensor = ThisWeekSensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test",
            startup_delay=0,
        )

        esb_data = MagicMock()
        esb_data.this_week = 85.7

        result = sensor._get_data(esb_data=esb_data)
        assert result == 85.7


class TestLast7DaysSensor:
    """Test Last7DaysSensor class."""

    @pytest.fixture
    def mock_esb_api(self):
        """Create mock ESB API."""
        return MagicMock()

    def test_unique_id(self, mock_esb_api):
        """Test Last 7 Days sensor unique ID."""
        sensor = Last7DaysSensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test",
            startup_delay=0,
        )

        assert sensor._attr_unique_id == "12345678901_last_7_days"

    def test_get_data(self, mock_esb_api):
        """Test Last 7 Days sensor gets correct data."""
        sensor = Last7DaysSensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test",
            startup_delay=0,
        )

        esb_data = MagicMock()
        esb_data.last_7_days = 175.2

        result = sensor._get_data(esb_data=esb_data)
        assert result == 175.2


class TestThisMonthSensor:
    """Test ThisMonthSensor class."""

    @pytest.fixture
    def mock_esb_api(self):
        """Create mock ESB API."""
        return MagicMock()

    def test_unique_id(self, mock_esb_api):
        """Test This Month sensor unique ID."""
        sensor = ThisMonthSensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test",
            startup_delay=0,
        )

        assert sensor._attr_unique_id == "12345678901_this_month"

    def test_get_data(self, mock_esb_api):
        """Test This Month sensor gets correct data."""
        sensor = ThisMonthSensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test",
            startup_delay=0,
        )

        esb_data = MagicMock()
        esb_data.this_month = 450.8

        result = sensor._get_data(esb_data=esb_data)
        assert result == 450.8


class TestLast30DaysSensor:
    """Test Last30DaysSensor class."""

    @pytest.fixture
    def mock_esb_api(self):
        """Create mock ESB API."""
        return MagicMock()

    def test_unique_id(self, mock_esb_api):
        """Test Last 30 Days sensor unique ID."""
        sensor = Last30DaysSensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test",
            startup_delay=0,
        )

        assert sensor._attr_unique_id == "12345678901_last_30_days"

    def test_get_data(self, mock_esb_api):
        """Test Last 30 Days sensor gets correct data."""
        sensor = Last30DaysSensor(
            esb_api=mock_esb_api,
            mprn="12345678901",
            name="Test",
            startup_delay=0,
        )

        esb_data = MagicMock()
        esb_data.last_30_days = 520.6

        result = sensor._get_data(esb_data=esb_data)
        assert result == 520.6
