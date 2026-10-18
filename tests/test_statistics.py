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
from custom_components.esb_smart_meter.statistics import async_import_hourly_statistics

IMPORT = "Active Import Interval (kWh)"
USAGE_ID = "sensor.esb_electricity_usage_history"


def _row(dt, value, read_type):
    return {"Read Date and End Time": dt.strftime("%d-%m-%Y %H:%M"), "Read Value": str(value), "Read Type": read_type}


@pytest.fixture
def hourly_usage():
    """Two complete usage hours: 0.3 then 0.4 kWh."""
    h = (datetime.now() - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    data = [
        _row(h + timedelta(minutes=30), 0.1, IMPORT),
        _row(h + timedelta(minutes=60), 0.2, IMPORT),
        _row(h + timedelta(minutes=90), 0.1, IMPORT),
        _row(h + timedelta(minutes=120), 0.3, IMPORT),
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


def _usage_rows(*labels_and_values):
    """Build ESB import rows from (Dublin wall-clock label, value) pairs."""
    return [
        {"Read Date and End Time": label, "Read Value": value, "Read Type": IMPORT}
        for label, value in labels_and_values
    ]


async def _stored_rows(hass, since):
    """Read the stored hourly statistics rows back from the recorder."""
    stats = await hass.async_add_executor_job(
        statistics_during_period, hass, since, None, {USAGE_ID}, "hour", None, {"state", "sum", "change"}
    )
    return stats[USAGE_ID]


def _starts(stats):
    """Return row start times as UTC datetimes (the recorder reports timestamps)."""
    return [datetime.fromtimestamp(row["start"], timezone.utc) for row in stats]


@pytest.mark.asyncio
async def test_first_import_publishes_only_complete_hours(recorder_mock, hass, models_now):
    """A cutoff inside the first hour must not publish leading or trailing halves."""
    # Default frozen clock: cutoff = 2026-04-16 12:37Z, inside the 12:00Z hour.
    rows = _usage_rows(
        ("16-04-2026 15:30", "0.9"),  # 14:30Z: only the first end of the 14:00Z hour
        ("16-04-2026 15:00", "2.0"),  # 14:00Z: second end of the 13:00Z hour
        ("16-04-2026 14:30", "1.0"),  # 13:30Z: first end of the 13:00Z hour
        ("16-04-2026 14:00", "0.5"),  # 13:00Z: second end of the 12:00Z hour, kept
        ("16-04-2026 13:30", "0.5"),  # 12:30Z: first end of the 12:00Z hour, cut off
        ("16-04-2026 13:00", "0.25"),  # 12:00Z: already outside the 90-day window
    )

    await async_import_hourly_statistics(hass, USAGE_ID, ESBData(data=rows).hourly_usage())
    await async_wait_recording_done(hass)

    stats = await _stored_rows(hass, datetime(2026, 4, 1, tzinfo=timezone.utc))
    assert _starts(stats) == [datetime(2026, 4, 16, 13, 0, tzinfo=timezone.utc)]
    assert [row["state"] for row in stats] == pytest.approx([3.0])
    assert [row["sum"] for row in stats] == pytest.approx([3.0])


@pytest.mark.asyncio
async def test_later_cutoff_preserves_complete_hours_and_suffix(recorder_mock, hass, models_now):
    """A refresh whose cutoff cuts the first hour must not rewrite it or shift later sums."""
    rows = _usage_rows(
        ("16-04-2026 15:00", "1.5"),  # 14:00Z: second end of the 13:00Z hour
        ("16-04-2026 14:30", "1.0"),  # 13:30Z: first end of the 13:00Z hour
        ("16-04-2026 14:00", "0.5"),  # 13:00Z: second end of the 12:00Z hour
        ("16-04-2026 13:30", "0.25"),  # 12:30Z: first end of the 12:00Z hour
    )

    # First publication: cutoff sits exactly on 12:00Z, so both hours are complete.
    models_now.frozen = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    await async_import_hourly_statistics(hass, USAGE_ID, ESBData(data=rows).hourly_usage())
    await async_wait_recording_done(hass)
    first = await _stored_rows(hass, datetime(2026, 4, 1, tzinfo=timezone.utc))
    assert _starts(first) == [
        datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 16, 13, 0, tzinfo=timezone.utc),
    ]
    assert [row["sum"] for row in first] == pytest.approx([0.75, 3.25])

    # Later refresh: the cutoff now falls inside 12:00Z, so only 13:00Z is admitted.
    models_now.frozen = datetime(2026, 7, 15, 12, 37, tzinfo=timezone.utc)
    await async_import_hourly_statistics(hass, USAGE_ID, ESBData(data=rows).hourly_usage())
    await async_wait_recording_done(hass)

    stats = await _stored_rows(hass, datetime(2026, 4, 1, tzinfo=timezone.utc))
    # The complete 12:00Z hour keeps its published sum; the later row continues from it.
    assert _starts(stats) == [
        datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 16, 13, 0, tzinfo=timezone.utc),
    ]
    assert [row["state"] for row in stats] == pytest.approx([0.75, 3.25])
    assert [row["sum"] for row in stats] == pytest.approx([0.75, 3.25])
    assert [row["change"] for row in stats] == pytest.approx([0.75, 2.5])
