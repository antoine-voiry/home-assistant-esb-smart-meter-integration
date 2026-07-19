"""Tests for long-term statistics import."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import homeassistant.util.dt as dt_util
import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.esb_smart_meter.models import ESBData
from custom_components.esb_smart_meter.statistics import async_import_hourly_statistics

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
