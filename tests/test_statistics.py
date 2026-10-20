"""Tests for long-term statistics import."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import homeassistant.util.dt as dt_util
import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.esb_smart_meter.models import ESBData
from custom_components.esb_smart_meter.statistics import (
    async_has_statistic_at,
    async_import_hourly_statistics,
    recent_hours,
)

IMPORT = "Active Import Interval (kWh)"
USAGE_ID = "sensor.esb_electricity_usage_history"


def _row(dt, value, read_type):
    return {"Read Date and End Time": dt.strftime("%d-%m-%Y %H:%M"), "Read Value": str(value), "Read Type": read_type}


@pytest.fixture
def hourly_usage():
    """Two usage hours: 0.3 then 0.4 kWh."""
    h = (datetime.now() - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    data = [
        _row(h + timedelta(minutes=30), 0.1, IMPORT),
        _row(h + timedelta(minutes=60), 0.2, IMPORT),
        _row(h + timedelta(minutes=90), 0.4, IMPORT),
    ]
    return ESBData(data=data).hourly_usage()


@pytest.mark.asyncio
async def test_first_import_builds_cumulative_sum(hourly_usage):
    """Test first import adds all hours with a running cumulative sum."""
    hass = MagicMock()
    with patch("custom_components.esb_smart_meter.statistics.get_instance") as get_instance, patch(
        "custom_components.esb_smart_meter.statistics.async_import_statistics"
    ) as add:
        get_instance.return_value.async_add_executor_job = AsyncMock(return_value={})
        await async_import_hourly_statistics(hass, USAGE_ID, hourly_usage)

    add.assert_called_once()
    _hass, metadata, stats = add.call_args.args
    assert metadata["statistic_id"] == USAGE_ID
    assert metadata["source"] == "recorder"
    assert [s["state"] for s in stats] == pytest.approx([0.3, 0.7])  # cumulative reading
    assert [s["sum"] for s in stats] == pytest.approx([0.3, 0.7])


@pytest.mark.asyncio
async def test_baseline_offsets_cumulative_sum(hourly_usage):
    """Test the cumulative continues from the baseline recorded before the window."""
    hass = MagicMock()

    async def fake_executor(func, *args):
        # statistics_during_period(...) -> last recorded row before the window
        return {USAGE_ID: [{"sum": 10.0}]}

    with patch("custom_components.esb_smart_meter.statistics.get_instance") as get_instance, patch(
        "custom_components.esb_smart_meter.statistics.async_import_statistics"
    ) as add:
        get_instance.return_value.async_add_executor_job = AsyncMock(side_effect=fake_executor)
        await async_import_hourly_statistics(hass, USAGE_ID, hourly_usage)

    # Full window is (re)imported, each hour offset by the baseline of 10.0
    _hass, _metadata, stats = add.call_args.args
    assert [s["state"] for s in stats] == pytest.approx([10.3, 10.7])
    assert [s["sum"] for s in stats] == pytest.approx([10.3, 10.7])


@pytest.mark.asyncio
async def test_statistics_written_to_recorder(recorder_mock, hass, hourly_usage):
    """Test statistics are persisted to and read back from the recorder."""
    await async_import_hourly_statistics(hass, USAGE_ID, hourly_usage)
    await async_wait_recording_done(hass)

    start = dt_util.utcnow() - timedelta(days=2)
    stats = await hass.async_add_executor_job(
        statistics_during_period, hass, start, None, {USAGE_ID}, "hour", None, {"state", "sum"}
    )

    assert [row["state"] for row in stats[USAGE_ID]] == pytest.approx([0.3, 0.7])
    assert [row["sum"] for row in stats[USAGE_ID]] == pytest.approx([0.3, 0.7])


@pytest.mark.asyncio
async def test_no_data_adds_nothing():
    """Test empty data adds no statistics."""
    hass = MagicMock()
    with patch("custom_components.esb_smart_meter.statistics.get_instance") as get_instance, patch(
        "custom_components.esb_smart_meter.statistics.async_import_statistics"
    ) as add:
        get_instance.return_value.async_add_executor_job = AsyncMock(return_value={})
        await async_import_hourly_statistics(hass, USAGE_ID, [])

    add.assert_not_called()


@pytest.mark.asyncio
async def test_metadata_declares_energy_unit_class(hourly_usage):
    """HA 2026.11 requires unit_class on imported statistics."""
    hass = MagicMock()
    with patch("custom_components.esb_smart_meter.statistics.get_instance") as get_instance, patch(
        "custom_components.esb_smart_meter.statistics.async_import_statistics"
    ) as add:
        get_instance.return_value.async_add_executor_job = AsyncMock(return_value={})
        await async_import_hourly_statistics(hass, USAGE_ID, hourly_usage)

    _hass, metadata, _stats = add.call_args.args
    assert metadata["unit_class"] == "energy"


@pytest.mark.asyncio
async def test_has_statistic_at(recorder_mock, hass, hourly_usage):
    await async_import_hourly_statistics(hass, USAGE_ID, hourly_usage)
    await async_wait_recording_done(hass)

    first_hour = hourly_usage[0][0]
    assert await async_has_statistic_at(hass, USAGE_ID, first_hour) is True
    assert await async_has_statistic_at(hass, USAGE_ID, first_hour - timedelta(hours=5)) is False


@pytest.mark.asyncio
async def test_monetary_metadata_has_no_unit_class(hourly_usage):
    hass = MagicMock()
    with patch("custom_components.esb_smart_meter.statistics.get_instance") as get_instance, patch(
        "custom_components.esb_smart_meter.statistics.async_import_statistics"
    ) as add:
        get_instance.return_value.async_add_executor_job = AsyncMock(return_value={})
        await async_import_hourly_statistics(hass, "sensor.cost", hourly_usage, unit="EUR")

    _hass, metadata, _stats = add.call_args.args
    assert metadata["unit_of_measurement"] == "EUR"
    assert metadata["unit_class"] is None


def test_recent_hours_keeps_last_days_of_download():
    newest = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    hourly = [(newest - timedelta(hours=h), 1.0) for h in range(400, -1, -1)]

    recent = recent_hours(hourly, days=7)

    assert recent[0][0] == newest - timedelta(days=7)
    assert recent[-1][0] == newest
    assert len(recent) == 7 * 24 + 1
    assert recent_hours([], days=7) == []
