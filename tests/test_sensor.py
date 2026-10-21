"""Integration tests for sensor.py with coordinator pattern."""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.esb_smart_meter.const import DOMAIN
from custom_components.esb_smart_meter.coordinator import ESBRuntimeData
from custom_components.esb_smart_meter.models import ESBData
from custom_components.esb_smart_meter.tariff import TariffPeriod, fingerprint, usage_costs
from custom_components.esb_smart_meter.sensor import (
    ApiStatusSensor,
    CircuitBreakerStatusSensor,
    DataAgeSensor,
    Last7DaysSensor,
    Last24HoursSensor,
    Last30DaysSensor,
    LastUpdateSensor,
    ThisMonthSensor,
    ThisWeekSensor,
    TodaySensor,
    ExportLast7DaysSensor,
    ExportLast24HoursSensor,
    ExportLast30DaysSensor,
    ExportThisMonthSensor,
    ExportThisWeekSensor,
    ExportTodaySensor,
    UsageHistorySensor,
    ExportHistorySensor,
    UsageTotalSensor,
    ExportTotalSensor,
    ExportCreditSensor,
    UsageCostSensor,
    async_setup_entry,
)
from tests.conftest import _async_create_task_handler

COMPONENT = Path(__file__).parent.parent / "custom_components" / "esb_smart_meter"


@pytest.fixture
def recorder_stub():
    """Stub the recorder helpers the history sensors call: stored hours, import, commit wait."""
    with patch(
        "custom_components.esb_smart_meter.sensor.async_stored_hours", new=AsyncMock(return_value=set())
    ) as stored, patch(
        "custom_components.esb_smart_meter.sensor.async_import_hourly_statistics", new=AsyncMock(return_value=1.0)
    ) as importer, patch(
        "custom_components.esb_smart_meter.sensor.async_wait_committed", new=AsyncMock(return_value=True)
    ) as committed:
        yield SimpleNamespace(stored=stored, importer=importer, committed=committed)


class TestAsyncSetupEntry:
    """Test async_setup_entry function with coordinator."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        coordinator = MagicMock(spec=DataUpdateCoordinator)
        now_str = datetime.now().strftime("%d-%m-%Y %H:%M")
        coordinator.data = ESBData(
            data=[
                {
                    "Read Date and End Time": now_str,
                    "Read Value": "1.5",
                    "Read Type": "Active Import Interval (kWh)",
                    "MPRN": "12345678901",
                },
                {
                    "Read Date and End Time": now_str,
                    "Read Value": "0.5",
                    "Read Type": "Active Export Interval (kWh)",
                    "MPRN": "12345678901",
                },
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
        # Mock async_create_task to properly close coroutines and prevent RuntimeWarnings
        hass.async_create_task = MagicMock(side_effect=_async_create_task_handler)
        return hass

    @pytest.fixture
    def mock_config_entry(self, mock_coordinator):
        """Create mock config entry."""
        entry = MagicMock(spec=ConfigEntry)
        entry.entry_id = "test_entry_id"
        entry.runtime_data = ESBRuntimeData(coordinator=mock_coordinator, session=MagicMock())
        entry.subentries = {}
        return entry

    @pytest.mark.asyncio
    async def test_setup_entry_creates_all_sensors(self, mock_hass, mock_config_entry):
        """Test that setup_entry creates all 22 sensors (period, export, history, cost, total, diagnostic)."""
        async_add_entities = MagicMock()

        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        # Verify 22 sensors were created (12 period + 2 history + 2 cost + 2 total + 4 diagnostic)
        assert async_add_entities.called
        sensors = async_add_entities.call_args[0][0]
        assert len(sensors) == 22

        # Verify sensor types
        assert isinstance(sensors[0], TodaySensor)
        assert isinstance(sensors[1], Last24HoursSensor)
        assert isinstance(sensors[2], ThisWeekSensor)
        assert isinstance(sensors[3], Last7DaysSensor)
        assert isinstance(sensors[4], ThisMonthSensor)
        assert isinstance(sensors[5], Last30DaysSensor)
        assert isinstance(sensors[6], ExportTodaySensor)
        assert isinstance(sensors[7], ExportLast24HoursSensor)
        assert isinstance(sensors[8], ExportThisWeekSensor)
        assert isinstance(sensors[9], ExportLast7DaysSensor)
        assert isinstance(sensors[10], ExportThisMonthSensor)
        assert isinstance(sensors[11], ExportLast30DaysSensor)
        # History (statistics) and their display totals
        assert isinstance(sensors[12], UsageHistorySensor)
        assert isinstance(sensors[13], ExportHistorySensor)
        assert isinstance(sensors[14], UsageCostSensor)
        assert isinstance(sensors[15], ExportCreditSensor)
        assert isinstance(sensors[16], UsageTotalSensor)
        assert isinstance(sensors[17], ExportTotalSensor)
        # Diagnostic sensors
        assert isinstance(sensors[18], LastUpdateSensor)
        assert isinstance(sensors[19], ApiStatusSensor)
        assert isinstance(sensors[20], DataAgeSensor)
        assert isinstance(sensors[21], CircuitBreakerStatusSensor)

    @pytest.mark.asyncio
    async def test_unique_ids_are_unchanged(self, mock_hass, mock_config_entry):
        """Registry entries (and so entity ids and statistics) are keyed on these; never change them."""
        async_add_entities = MagicMock()
        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        suffixes = [s.unique_id.removeprefix("12345678901_") for s in async_add_entities.call_args[0][0]]
        assert suffixes == [
            "today", "last_24_hours", "this_week", "last_7_days", "this_month", "last_30_days",
            "export_today", "export_last_24_hours", "export_this_week", "export_last_7_days",
            "export_this_month", "export_last_30_days",
            "usage_history", "export_history", "usage_cost_total", "export_credit_total", "usage_total", "export_total",
            "last_update", "api_status", "data_age", "circuit_breaker_status",
        ]

    @pytest.mark.asyncio
    async def test_sensors_use_translated_entity_names(self, mock_hass, mock_config_entry):
        """Names come from translations under the device name, not hard-coded strings."""
        names = json.loads((COMPONENT / "translations" / "en.json").read_text())["entity"]["sensor"]
        async_add_entities = MagicMock()
        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        sensors = async_add_entities.call_args[0][0]
        keys = [s.translation_key for s in sensors]
        assert len(set(keys)) == len(sensors)
        for sensor in sensors:
            assert sensor.has_entity_name is True
            assert not hasattr(sensor, "_attr_name"), type(sensor).__name__
            assert names[sensor.translation_key]["name"].strip()

    @pytest.mark.asyncio
    async def test_status_sensors_are_diagnostic(self, mock_hass, mock_config_entry):
        async_add_entities = MagicMock()
        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        sensors = async_add_entities.call_args[0][0]
        diagnostic = (LastUpdateSensor, ApiStatusSensor, DataAgeSensor, CircuitBreakerStatusSensor)
        for sensor in sensors:
            expected = EntityCategory.DIAGNOSTIC if isinstance(sensor, diagnostic) else None
            assert sensor.entity_category == expected, type(sensor).__name__


class TestBaseSensor:
    """Test BaseSensor class with coordinator."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator."""
        coordinator = MagicMock(spec=DataUpdateCoordinator)
        now_str = datetime.now().strftime("%d-%m-%Y %H:%M")
        coordinator.data = ESBData(
            data=[
                {
                    "Read Date and End Time": now_str,
                    "Read Value": "1.5",
                    "Read Type": "Active Import Interval (kWh)",
                    "MPRN": "12345678901",
                },
                {
                    "Read Date and End Time": now_str,
                    "Read Value": "0.5",
                    "Read Type": "Active Export Interval (kWh)",
                    "MPRN": "12345678901",
                },
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

    def test_export_sensor_reads_export_only(self):
        """Test export sensor reads export data, not import."""
        ts = datetime.now().strftime("%d-%m-%Y %H:%M")
        coordinator = MagicMock(spec=DataUpdateCoordinator)
        coordinator.data = ESBData(
            data=[
                {"Read Date and End Time": ts, "Read Value": "1.5", "Read Type": "Active Import Interval (kWh)"},
                {"Read Date and End Time": ts, "Read Value": "0.5", "Read Type": "Active Export Interval (kWh)"},
            ]
        )

        export_sensor = ExportTodaySensor(coordinator=coordinator, mprn="12345678901")
        import_sensor = TodaySensor(coordinator=coordinator, mprn="12345678901")

        assert export_sensor.native_value == 0.5
        assert import_sensor.native_value == 1.5
        assert export_sensor.unique_id == "12345678901_export_today"

    def test_history_sensor_config(self, mock_coordinator):
        """Test history sensor id; it stays stateless (no auto-compiled stats)."""
        usage_history = UsageHistorySensor(coordinator=mock_coordinator, mprn="12345678901")

        assert usage_history.unique_id == "12345678901_usage_history"
        # No state_class, so the recorder won't auto-compile a competing statistics series
        assert usage_history.state_class is None
        assert usage_history.native_value is None

    def test_total_sensor_config(self, mock_coordinator):
        """Test the display total sensor id and that it carries no statistics."""
        usage_history = UsageHistorySensor(coordinator=mock_coordinator, mprn="12345678901")
        usage_total = UsageTotalSensor(
            coordinator=mock_coordinator, mprn="12345678901", history=usage_history
        )

        assert usage_total.unique_id == "12345678901_usage_total"
        # Display only: no state_class means no auto-compiled statistics for this entity
        assert usage_total.state_class is None
        assert usage_total.native_value is None

    @pytest.mark.asyncio
    async def test_history_import_updates_total(self, mock_coordinator, recorder_stub):
        """Test importing history refreshes the paired total sensor's value."""
        usage_history = UsageHistorySensor(coordinator=mock_coordinator, mprn="12345678901")
        usage_total = UsageTotalSensor(
            coordinator=mock_coordinator, mprn="12345678901", history=usage_history
        )
        usage_history.hass = MagicMock()
        usage_history.entity_id = "sensor.esb_electricity_usage_history"
        usage_total.hass = MagicMock()
        usage_total.async_write_ha_state = Mock()
        recorder_stub.stored.return_value = {h for h, _ in mock_coordinator.data.hourly_usage()}
        recorder_stub.importer.return_value = 6.641

        await usage_history._async_import_statistics()

        recorder_stub.importer.assert_awaited_once()
        assert usage_total.native_value == 6.641
        usage_total.async_write_ha_state.assert_called_once()

    @pytest.mark.parametrize(
        "sensor_cls, prop",
        [
            (ExportTodaySensor, "export_today"),
            (ExportLast24HoursSensor, "export_last_24_hours"),
            (ExportThisWeekSensor, "export_this_week"),
            (ExportLast7DaysSensor, "export_last_7_days"),
            (ExportThisMonthSensor, "export_this_month"),
            (ExportLast30DaysSensor, "export_last_30_days"),
        ],
    )
    def test_export_sensors_value_and_readings(self, sensor_cls, prop):
        """Test export sensors read value and readings from export data."""
        ts = datetime.now().strftime("%d-%m-%Y %H:%M")
        coordinator = MagicMock(spec=DataUpdateCoordinator)
        coordinator.data = ESBData(
            data=[
                {"Read Date and End Time": ts, "Read Value": "1.5", "Read Type": "Active Import Interval (kWh)"},
                {"Read Date and End Time": ts, "Read Value": "0.5", "Read Type": "Active Export Interval (kWh)"},
            ]
        )
        sensor = sensor_cls(coordinator=coordinator, mprn="12345678901")
        assert sensor.native_value == getattr(coordinator.data, prop)
        readings = sensor.extra_state_attributes["readings"]
        assert readings == [{"timestamp": readings[0]["timestamp"], "value": 0.5}]

    def test_sensor_extra_state_attributes(self, mock_coordinator):
        """Test sensor extra state attributes contain readings."""
        sensor = TodaySensor(coordinator=mock_coordinator, mprn="12345678901")
        attrs = sensor.extra_state_attributes
        assert attrs is not None
        assert "readings" in attrs
        assert len(attrs["readings"]) > 0

    def test_sensor_extra_state_attributes_no_data(self):
        """Test extra state attributes return None when no data."""
        coordinator = MagicMock(spec=DataUpdateCoordinator)
        coordinator.data = None
        sensor = TodaySensor(coordinator=coordinator, mprn="12345678901")
        assert sensor.extra_state_attributes is None

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

    def test_get_readings(self, mock_coordinator):
        """Test Today sensor get_readings calls model."""
        sensor = TodaySensor(coordinator=mock_coordinator, mprn="12345678901")
        esb_data = MagicMock()
        sensor._get_readings(esb_data=esb_data)
        assert esb_data.get_readings_since.called


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

    def test_get_readings(self, mock_coordinator):
        """Test Last 24 Hours sensor get_readings calls model."""
        sensor = Last24HoursSensor(coordinator=mock_coordinator, mprn="12345678901")
        esb_data = MagicMock()
        sensor._get_readings(esb_data=esb_data)
        assert esb_data.get_readings_since.called


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

    def test_get_readings(self, mock_coordinator):
        """Test This Week sensor get_readings calls model."""
        sensor = ThisWeekSensor(coordinator=mock_coordinator, mprn="12345678901")
        esb_data = MagicMock()
        sensor._get_readings(esb_data=esb_data)
        assert esb_data.get_readings_since.called


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

    def test_get_readings(self, mock_coordinator):
        """Test Last 7 Days sensor get_readings calls model."""
        sensor = Last7DaysSensor(coordinator=mock_coordinator, mprn="12345678901")
        esb_data = MagicMock()
        sensor._get_readings(esb_data=esb_data)
        assert esb_data.get_readings_since.called


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

    def test_get_readings(self, mock_coordinator):
        """Test This Month sensor get_readings calls model."""
        sensor = ThisMonthSensor(coordinator=mock_coordinator, mprn="12345678901")
        esb_data = MagicMock()
        sensor._get_readings(esb_data=esb_data)
        assert esb_data.get_readings_since.called


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

    def test_get_readings(self, mock_coordinator):
        """Test Last 30 Days sensor get_readings calls model."""
        sensor = Last30DaysSensor(coordinator=mock_coordinator, mprn="12345678901")
        esb_data = MagicMock()
        sensor._get_readings(esb_data=esb_data)
        assert esb_data.get_readings_since.called


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
        """Test Last Update sensor returns datetime."""
        from datetime import datetime, timezone

        test_time = datetime(2024, 12, 31, 12, 30, 0, tzinfo=timezone.utc)
        mock_coordinator.last_successful_update_time = test_time
        sensor = LastUpdateSensor(coordinator=mock_coordinator, mprn="12345678901")

        assert sensor.native_value == test_time


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


class TestCircuitBreakerStatusSensor:
    """Test CircuitBreakerStatusSensor class."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock coordinator with circuit breaker."""
        coordinator = MagicMock()
        coordinator.mprn = "12345678901"
        cb = MagicMock()
        cb._is_open = False
        cb._failure_count = 0
        cb._daily_attempts = 0
        cb._daily_attempts_reset_time = None
        cb._last_failure_time = None
        coordinator.esb_api._circuit_breaker = cb
        return coordinator

    def test_sensor_init(self, mock_coordinator):
        """Test initialization."""
        sensor = CircuitBreakerStatusSensor(coordinator=mock_coordinator, mprn="12345678901")
        assert sensor.translation_key == "circuit_breaker_status"
        assert sensor._attr_unique_id == "12345678901_circuit_breaker_status"

    def test_native_value_closed(self, mock_coordinator):
        """Test value when closed."""
        sensor = CircuitBreakerStatusSensor(coordinator=mock_coordinator, mprn="12345678901")
        assert sensor.native_value == "closed"
        assert sensor.icon == "mdi:check-circle"

    def test_native_value_unknown(self, mock_coordinator):
        """Test value when cb has no attributes."""
        del mock_coordinator.esb_api._circuit_breaker._is_open
        sensor = CircuitBreakerStatusSensor(coordinator=mock_coordinator, mprn="12345678901")
        assert sensor.native_value == "unknown"
        assert sensor.icon == "mdi:help-circle"

    def test_native_value_open(self, mock_coordinator):
        """Test value when open."""
        from datetime import datetime
        cb = mock_coordinator.esb_api._circuit_breaker
        cb._is_open = True
        cb._last_failure_time = datetime.now()
        cb._failure_count = 1
        sensor = CircuitBreakerStatusSensor(coordinator=mock_coordinator, mprn="12345678901")
        assert sensor.native_value == "open"
        assert sensor.icon == "mdi:alert-circle"

    def test_native_value_half_open(self, mock_coordinator):
        """Test value when half-open."""
        from datetime import datetime, timedelta
        cb = mock_coordinator.esb_api._circuit_breaker
        cb._is_open = True
        cb._last_failure_time = datetime.now() - timedelta(hours=1)
        cb._failure_count = 1
        sensor = CircuitBreakerStatusSensor(coordinator=mock_coordinator, mprn="12345678901")
        assert sensor.native_value == "half_open"
        assert sensor.icon == "mdi:refresh-circle"

    def test_extra_state_attributes(self, mock_coordinator):
        """Test extra state attributes."""
        from datetime import datetime
        cb = mock_coordinator.esb_api._circuit_breaker
        cb._is_open = True
        cb._last_failure_time = datetime.now()
        cb._daily_attempts_reset_time = datetime.now()
        sensor = CircuitBreakerStatusSensor(coordinator=mock_coordinator, mprn="12345678901")
        attrs = sensor.extra_state_attributes
        assert attrs["failure_count"] == 0
        assert attrs["daily_attempts"] == 0
        assert "backoff_seconds" in attrs
        assert "blocked_until" in attrs

    def test_extra_state_attributes_no_attrs(self, mock_coordinator):
        """Test extra state attributes when cb has no attributes."""
        del mock_coordinator.esb_api._circuit_breaker._failure_count
        sensor = CircuitBreakerStatusSensor(coordinator=mock_coordinator, mprn="12345678901")
        assert sensor.extra_state_attributes == {}


PERIOD_SENSORS = [
    TodaySensor, Last24HoursSensor, ThisWeekSensor, Last7DaysSensor, ThisMonthSensor, Last30DaysSensor,
    ExportTodaySensor, ExportLast24HoursSensor, ExportThisWeekSensor, ExportLast7DaysSensor,
    ExportThisMonthSensor, ExportLast30DaysSensor,
]


@pytest.mark.parametrize("sensor_cls", PERIOD_SENSORS)
def test_period_sensor_has_no_state_class(sensor_cls):
    """Moving windows must not compile long-term statistics (negative sums, #12)."""
    sensor = sensor_cls(coordinator=MagicMock(spec=DataUpdateCoordinator), mprn="12345678901")
    assert sensor.state_class is None


@pytest.mark.parametrize("sensor_cls", PERIOD_SENSORS)
def test_period_sensor_readings_not_recorded(sensor_cls):
    """readings exceeds the recorder's 16 KB limit, which drops the unit too (#12)."""
    sensor = sensor_cls(coordinator=MagicMock(spec=DataUpdateCoordinator), mprn="12345678901")
    assert "readings" in sensor._unrecorded_attributes


class TestHistorySensorRefreshHooks:
    """History sensors re-import statistics whenever the coordinator refreshes."""

    def _sensor(self, data):
        coordinator = MagicMock(spec=DataUpdateCoordinator)
        coordinator.data = data
        coordinator.config_entry = MagicMock()
        sensor = UsageHistorySensor(coordinator=coordinator, mprn="12345678901")
        sensor.hass = MagicMock()
        sensor.entity_id = "sensor.esb_electricity_usage_history"
        return sensor

    @staticmethod
    def _ten_days():
        end = datetime.now().replace(minute=0, second=0, microsecond=0)
        rows = []
        for h in range(240):
            label = (end - timedelta(hours=h)).strftime("%d-%m-%Y %H:%M")
            rows.append({"Read Date and End Time": label, "Read Value": "0.5", "Read Type": "Active Import Interval (kWh)"})
        return ESBData(data=rows)

    @pytest.mark.asyncio
    async def test_first_import_is_full(self, recorder_stub):
        sensor = self._sensor(data=self._ten_days())
        hourly = sensor.coordinator.data.hourly_usage()

        await sensor._async_import_statistics()

        assert recorder_stub.importer.call_args.args[2] == hourly
        # The stored-hours query covers exactly the downloaded range
        _hass, statistic_id, start, end = recorder_stub.stored.call_args.args
        assert (statistic_id, start, end) == (sensor.entity_id, hourly[0][0], hourly[-1][0] + timedelta(hours=1))

    @pytest.mark.asyncio
    async def test_later_imports_cover_recent_days_only(self, recorder_stub):
        sensor = self._sensor(data=self._ten_days())
        recorder_stub.stored.return_value = {h for h, _ in sensor.coordinator.data.hourly_usage()}

        await sensor._async_import_statistics()

        hours = recorder_stub.importer.call_args.args[2]
        assert hours[-1][0] - hours[0][0] == timedelta(days=7)
        assert recorder_stub.importer.call_args.kwargs["unit"] == "kWh"

    @pytest.mark.asyncio
    async def test_later_imports_resume_from_last_stored_hour(self, recorder_stub):
        """After an outage longer than the window, import from the hour after the last stored one."""
        sensor = self._sensor(data=self._ten_days())
        hourly = sensor.coordinator.data.hourly_usage()
        last_stored = hourly[-1][0] - timedelta(days=9)
        recorder_stub.stored.return_value = {h for h, _ in hourly if h <= last_stored}

        await sensor._async_import_statistics()

        hours = recorder_stub.importer.call_args.args[2]
        assert hours[0][0] == last_stored + timedelta(hours=1)
        assert hours[-1][0] == hourly[-1][0]

    @pytest.mark.asyncio
    async def test_hours_filled_in_late_trigger_full_import(self, recorder_stub):
        """ESB filling a gap older than the recent window must not leave it missing forever."""
        sensor = self._sensor(data=self._ten_days())
        hourly = sensor.coordinator.data.hourly_usage()
        late_filled = hourly[24][0]  # 9 days before the newest hour
        recorder_stub.stored.return_value = {h for h, _ in hourly} - {late_filled}

        await sensor._async_import_statistics()

        assert recorder_stub.importer.call_args.args[2] == hourly

    @pytest.mark.asyncio
    async def test_hours_after_newest_stored_are_not_a_gap(self, recorder_stub):
        """New data since the last refresh is a normal recent import, not a backfill."""
        sensor = self._sensor(data=self._ten_days())
        hourly = sensor.coordinator.data.hourly_usage()
        recorder_stub.stored.return_value = {h for h, _ in hourly[:-30]}

        await sensor._async_import_statistics()

        assert len(recorder_stub.importer.call_args.args[2]) < len(hourly)

    @pytest.mark.asyncio
    async def test_import_waits_for_recorder_commit(self, recorder_stub):
        sensor = self._sensor(data=self._ten_days())

        await sensor._async_import_statistics()

        hourly = sensor.coordinator.data.hourly_usage()
        recorder_stub.committed.assert_awaited_once_with(sensor.hass, sensor.entity_id, hourly[-1][0], 1.0)

    @pytest.mark.asyncio
    async def test_concurrent_imports_run_one_at_a_time(self, recorder_stub):
        """A refresh during an import waits, so it reads committed sums, not stale ones."""
        sensor = self._sensor(data=self._ten_days())
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_commit(*_args):
            started.set()
            await release.wait()
            return True

        recorder_stub.committed.side_effect = slow_commit
        first = asyncio.create_task(sensor._async_import_statistics())
        await started.wait()
        second = asyncio.create_task(sensor._async_import_statistics())
        done, _pending = await asyncio.wait([second], timeout=0.05)

        assert not done
        assert recorder_stub.importer.await_count == 1
        release.set()
        await asyncio.gather(first, second)
        assert recorder_stub.importer.await_count == 2

    def test_coordinator_update_schedules_background_import(self):
        sensor = self._sensor(data=None)
        with patch.object(sensor, "async_write_ha_state"):
            sensor._handle_coordinator_update()
        create = sensor.coordinator.config_entry.async_create_background_task
        create.assert_called_once()
        hass, coro, name = create.call_args.args
        assert hass is sensor.hass
        assert name == "sensor.esb_electricity_usage_history statistics import"
        coro.close()

    @pytest.mark.asyncio
    async def test_added_to_hass_schedules_import_without_waiting(self):
        """A two-year backfill runs in the background instead of holding up platform setup."""
        sensor = self._sensor(data=None)
        with patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass", new=AsyncMock()
        ):
            await sensor.async_added_to_hass()
        create = sensor.coordinator.config_entry.async_create_background_task
        create.assert_called_once()
        create.call_args.args[1].close()

    @pytest.mark.asyncio
    async def test_import_skipped_without_data(self, recorder_stub):
        sensor = self._sensor(data=None)
        await sensor._async_import_statistics()
        recorder_stub.importer.assert_not_awaited()
        recorder_stub.committed.assert_not_awaited()

    def test_history_sensor_is_stateless_and_on_device(self):
        sensor = self._sensor(data=None)
        assert sensor.native_value is None
        assert (DOMAIN, "12345678901") in sensor.device_info["identifiers"]


class TestCostSensors:
    """Cost series: external statistics, display totals, re-costing and zeroing."""

    @staticmethod
    def _entry(*periods):
        entry = MagicMock()
        entry.entry_id = "entry1"
        entry.subentries = {
            str(i): SimpleNamespace(subentry_type="tariff", data=p.as_data()) for i, p in enumerate(periods)
        }
        return entry

    @staticmethod
    def _coordinator():
        coordinator = MagicMock(spec=DataUpdateCoordinator)
        coordinator.config_entry = MagicMock()
        label = (datetime.now() - timedelta(days=1)).replace(minute=0).strftime("%d-%m-%Y %H:%M")
        coordinator.data = ESBData(
            data=[
                {"Read Date and End Time": label, "Read Value": "1.0", "Read Type": "Active Import Interval (kWh)"},
                {"Read Date and End Time": label, "Read Value": "0.5", "Read Type": "Active Export Interval (kWh)"},
            ]
        )
        return coordinator

    def _sensor(self, cls, *periods):
        sensor = cls(coordinator=self._coordinator(), mprn="12345678901", entry=self._entry(*periods))
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = Mock()
        return sensor

    PERIOD = TariffPeriod(
        effective_from=date(2020, 1, 1), day_rate=0.3, night_rate=0.2, peak_rate=0.4, standing_charge=0.72,
        export_rate=0.2,
    )

    @staticmethod
    def _state(stored_fingerprint, first_hour):
        state = MagicMock()
        state.async_get_fingerprint = AsyncMock(return_value=stored_fingerprint)
        state.async_get_first_hour = AsyncMock(return_value=first_hour)
        state.async_set_fingerprint = AsyncMock()
        return state

    # 2026-01-10 00:00 to 2026-01-11 23:00 UTC (= Dublin in winter), 1 kWh per hour
    OLDEST = datetime(2026, 1, 10, tzinfo=timezone.utc)
    NEWEST = datetime(2026, 1, 11, 23, tzinfo=timezone.utc)

    def _two_days_sensor(self, *periods):
        sensor = self._sensor(UsageCostSensor, *periods)
        start = datetime(2026, 1, 10, 0, 30)
        sensor.coordinator.data = ESBData(
            data=[
                {
                    "Read Date and End Time": (start + timedelta(minutes=30 * i)).strftime("%d-%m-%Y %H:%M"),
                    "Read Value": "0.5",
                    "Read Type": "Active Import Interval (kWh)",
                }
                for i in range(96)
            ]
        )
        return sensor

    async def _full_recost(self, sensor, previous_first, recorder_stub):
        """Re-cost after a tariff change, with every costed hour already stored."""
        state = self._state("stale", previous_first)
        hourly = sensor._hourly(esb_data=sensor.coordinator.data)
        recorder_stub.stored.return_value = {h for h, _ in hourly}
        with patch("custom_components.esb_smart_meter.sensor.ImportState", return_value=state):
            await sensor._async_import_statistics()
        importer = recorder_stub.importer
        return (importer.call_args.args[2] if importer.await_count else []), state

    def test_statistics_are_external_and_named(self):
        usage = self._sensor(UsageCostSensor, self.PERIOD)
        export = self._sensor(ExportCreditSensor, self.PERIOD)
        assert usage._statistic_id == "esb_smart_meter:usage_cost_12345678901"
        assert usage._statistic_name == "ESB Smart Meter (12345678901) Usage cost"
        assert export._statistic_id == "esb_smart_meter:export_credit_12345678901"
        assert export._statistic_name == "ESB Smart Meter (12345678901) Export credit"

    def test_entities_display_the_running_total(self):
        sensor = self._sensor(UsageCostSensor, self.PERIOD)
        assert sensor.unique_id == "12345678901_usage_cost_total"
        assert sensor.translation_key == "usage_cost_total"
        assert sensor.entity_id == "sensor.esb_electricity_usage_cost_total"
        assert sensor.device_class == SensorDeviceClass.MONETARY
        assert sensor.native_unit_of_measurement == "EUR"
        assert sensor.state_class is None  # no auto-compiled statistics competing with the import
        assert sensor.native_value is None
        export = self._sensor(ExportCreditSensor, self.PERIOD)
        assert export.unique_id == "12345678901_export_credit_total"
        assert export.entity_id == "sensor.esb_electricity_export_credit_total"

    @pytest.mark.asyncio
    async def test_import_targets_external_statistic_and_updates_state(self, recorder_stub):
        sensor = self._sensor(UsageCostSensor, self.PERIOD)
        recorder_stub.importer.return_value = 12.5
        with patch("custom_components.esb_smart_meter.sensor.ImportState", return_value=self._state("stale", None)):
            await sensor._async_import_statistics()

        args, kwargs = recorder_stub.importer.call_args
        assert args[1] == "esb_smart_meter:usage_cost_12345678901"
        assert kwargs == {"unit": "EUR", "name": "ESB Smart Meter (12345678901) Usage cost"}
        assert recorder_stub.stored.call_args.args[1] == "esb_smart_meter:usage_cost_12345678901"
        assert sensor.native_value == 12.5
        sensor.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_first_hour_moved_later_zeroes_hours_no_longer_costed(self, recorder_stub):
        later = TariffPeriod(effective_from=date(2026, 1, 11), day_rate=0.3, night_rate=0.3, peak_rate=0.3, standing_charge=0.0)
        sensor = self._two_days_sensor(later)

        series, state = await self._full_recost(sensor, self.OLDEST + timedelta(hours=2), recorder_stub)

        new_first = datetime(2026, 1, 11, tzinfo=timezone.utc)
        zeros = [row for row in series if row[0] < new_first]
        assert [hour for hour, _ in zeros] == [self.OLDEST + timedelta(hours=h) for h in range(2, 24)]
        assert all(value == 0.0 for _, value in zeros)
        assert series[len(zeros)] == (new_first, pytest.approx(0.3))
        assert series[-1][0] == self.NEWEST
        state.async_set_fingerprint.assert_awaited_once_with(fingerprint([later]), new_first)

    @pytest.mark.asyncio
    async def test_zeroing_starts_at_oldest_downloaded_hour(self, recorder_stub):
        """Hours older than the download can't be rewritten."""
        later = TariffPeriod(effective_from=date(2026, 1, 11), day_rate=0.3, night_rate=0.3, peak_rate=0.3, standing_charge=0.0)
        sensor = self._two_days_sensor(later)

        series, _state = await self._full_recost(sensor, datetime(2025, 6, 1, tzinfo=timezone.utc), recorder_stub)

        assert series[0] == (self.OLDEST, 0.0)
        assert len(series) == 48

    @pytest.mark.asyncio
    async def test_all_periods_removed_zeroes_previously_costed_range(self, recorder_stub):
        sensor = self._two_days_sensor()

        series, state = await self._full_recost(sensor, self.OLDEST + timedelta(hours=12), recorder_stub)

        assert [hour for hour, _ in series] == [self.OLDEST + timedelta(hours=h) for h in range(12, 48)]
        assert all(value == 0.0 for _, value in series)
        state.async_set_fingerprint.assert_awaited_once_with(fingerprint([]), None)

    @pytest.mark.asyncio
    async def test_first_hour_moved_earlier_adds_no_zero_rows(self, recorder_stub):
        sensor = self._two_days_sensor(self.PERIOD)

        series, state = await self._full_recost(sensor, self.OLDEST + timedelta(hours=24), recorder_stub)

        assert series == usage_costs(sensor.coordinator.data.hourly_usage(), [self.PERIOD])
        assert all(value > 0 for _, value in series)
        state.async_set_fingerprint.assert_awaited_once_with(fingerprint([self.PERIOD]), self.OLDEST)

    @pytest.mark.asyncio
    async def test_no_periods_and_nothing_costed_before_imports_nothing(self, recorder_stub):
        sensor = self._two_days_sensor()

        series, state = await self._full_recost(sensor, None, recorder_stub)

        assert series == []
        recorder_stub.committed.assert_not_awaited()
        state.async_set_fingerprint.assert_awaited_once_with(fingerprint([]), None)

    @pytest.mark.asyncio
    async def test_fingerprint_saved_after_commit(self, recorder_stub):
        """The fingerprint must not claim a re-cost the recorder hasn't committed."""
        sensor = self._sensor(UsageCostSensor, self.PERIOD)
        state = self._state("stale", None)
        order = []
        recorder_stub.committed.side_effect = lambda *_args: order.append("commit") or True
        state.async_set_fingerprint.side_effect = lambda *a: order.append("fingerprint")
        with patch("custom_components.esb_smart_meter.sensor.ImportState", return_value=state):
            await sensor._async_import_statistics()

        assert order == ["commit", "fingerprint"]
        first_hour = sensor.coordinator.data.hourly_usage()[0][0]
        state.async_set_fingerprint.assert_awaited_once_with(fingerprint([self.PERIOD]), first_hour)

    @pytest.mark.asyncio
    async def test_fingerprint_not_saved_when_import_not_readable(self, recorder_stub):
        """If the recorder never shows the re-cost, the next refresh must re-cost again."""
        sensor = self._sensor(UsageCostSensor, self.PERIOD)
        state = self._state("stale", None)
        recorder_stub.committed.return_value = False
        with patch("custom_components.esb_smart_meter.sensor.ImportState", return_value=state):
            await sensor._async_import_statistics()

        state.async_set_fingerprint.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_tariffs_imports_nothing(self, recorder_stub):
        sensor = self._sensor(UsageCostSensor)
        with patch("custom_components.esb_smart_meter.sensor.ImportState", return_value=self._state(None, None)):
            await sensor._async_import_statistics()
        recorder_stub.importer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unchanged_tariffs_import_recent_only(self, recorder_stub):
        sensor = self._sensor(ExportCreditSensor, self.PERIOD)
        hourly = sensor._hourly(esb_data=sensor.coordinator.data)
        recorder_stub.stored.return_value = {h for h, _ in hourly}
        state = self._state(fingerprint([self.PERIOD]), None)
        with patch("custom_components.esb_smart_meter.sensor.ImportState", return_value=state):
            await sensor._async_import_statistics()

        hours = recorder_stub.importer.call_args.args[2]
        assert [round(v, 6) for _, v in hours] == [0.1]  # 0.5 kWh x EUR 0.20
        state.async_set_fingerprint.assert_not_awaited()
