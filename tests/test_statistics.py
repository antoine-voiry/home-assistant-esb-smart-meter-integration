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
from custom_components.esb_smart_meter.sensor import (
    ExportHistorySensor, ExportTotalSensor, UsageHistorySensor, UsageTotalSensor,
)
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


async def _stored_rows(hass, statistic_id=USAGE_ID):
    """Read actual stored state/sum/change, including the preceding baseline."""
    stats = await hass.async_add_executor_job(
        statistics_during_period, hass, datetime(2026, 4, 1, tzinfo=timezone.utc), None,
        {statistic_id}, "hour", None, {"state", "sum", "change"}
    )
    return stats.get(statistic_id, [])


@pytest.mark.asyncio
async def test_first_import_publishes_only_complete_hours(recorder_mock, hass, models_now):
    """Cutoff 12:37Z removes the leading half; the trailing half waits for refresh."""
    h = datetime(2026, 4, 16, 13)  # Dublin: one hour ahead of UTC
    rows = [_row(h + timedelta(minutes=30 * i), value, IMPORT)
            for i, value in enumerate([0.25, 0.5, 0.5, 1, 2, 0.9])]
    assert await async_import_hourly_statistics(hass, USAGE_ID, ESBData(data=rows).hourly_usage()) == 3
    await async_wait_recording_done(hass)
    stats = await _stored_rows(hass)
    assert [row["start"] for row in stats] == [datetime(2026, 4, 16, 13, tzinfo=timezone.utc).timestamp()]
    assert [row["sum"] for row in stats] == [3]
    rows.append(_row(h + timedelta(hours=3), 0.1, IMPORT))
    assert await async_import_hourly_statistics(hass, USAGE_ID, ESBData(data=rows).hourly_usage()) == 4
    await async_wait_recording_done(hass)
    assert [row["change"] for row in await _stored_rows(hass)] == pytest.approx([3, 1])


@pytest.mark.asyncio
async def test_later_cutoff_preserves_complete_hours_and_suffix(recorder_mock, hass, models_now):
    """A moving cutoff must not lower the old boundary hour or shift its suffix."""
    h = datetime(2026, 4, 16, 13)
    rows = [_row(h + timedelta(minutes=30 * i), value, IMPORT)
            for i, value in enumerate([0.25, 0.5, 1, 1.5], start=1)]
    models_now.now.return_value = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    assert await async_import_hourly_statistics(hass, USAGE_ID, ESBData(data=rows).hourly_usage()) == 3.25
    await async_wait_recording_done(hass)
    before = await _stored_rows(hass)
    assert [row["sum"] for row in before] == [0.75, 3.25]
    models_now.now.return_value += timedelta(minutes=37)
    assert await async_import_hourly_statistics(hass, USAGE_ID, ESBData(data=rows).hourly_usage()) == 3.25
    await async_wait_recording_done(hass)
    assert await _stored_rows(hass) == before
    assert [row["change"] for row in before] == [0.75, 2.5]


@pytest.mark.asyncio
@pytest.mark.parametrize("seeded", [False, True])
@pytest.mark.parametrize("direction,history_cls,total_cls", [
    ("Import", UsageHistorySensor, UsageTotalSensor), ("Export", ExportHistorySensor, ExportTotalSensor),
])
async def test_internal_hole_is_imported_not_deferred(recorder_mock, hass, models_now, seeded,
                                                     direction, history_cls, total_cls):
    """ESB gaps are permanent: an hour missing a half keeps the half that exists and the import goes ahead."""
    models_now.now.return_value = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    h = datetime(2026, 4, 16, 13)
    rows = [_row(h + timedelta(minutes=30 * i), value, f"Active {direction} Interval (kWh)")
            for i, value in enumerate([0.5, 0.5, 1, 1, 1.5, 1.5], start=1)]
    coordinator = MagicMock(data=ESBData(data=rows))
    history = history_cls(coordinator=coordinator, mprn="12345678901")
    history.hass, history.entity_id = hass, f"sensor.esb_{direction.lower()}_history"
    total = total_cls(coordinator=coordinator, mprn="12345678901", history=history)
    total.async_write_ha_state = MagicMock()
    if seeded:
        await history._async_import_statistics()
        await async_wait_recording_done(hass)
        assert total.native_value == 6
    coordinator.data = ESBData(data=rows[:2] + rows[3:])  # One end of the middle hour never arrives.
    hourly = history._hourly(esb_data=coordinator.data)
    assert [kwh for _, kwh in hourly] == pytest.approx([1, 1, 3])

    await history._async_import_statistics()
    await async_wait_recording_done(hass)

    assert [row["sum"] for row in await _stored_rows(hass, history.entity_id)] == pytest.approx([1, 2, 5])
    assert total.native_value == pytest.approx(5)


@pytest.mark.asyncio
async def test_missing_hour_does_not_block_import(recorder_mock, hass):
    """A whole hour absent from the download (permanent ESB gap) leaves a hole, not a frozen dashboard."""
    start = datetime(2026, 4, 16, 13, tzinfo=timezone.utc)
    hourly = [(start, 1.0), (start + timedelta(hours=3), 2.0), (start + timedelta(hours=4), 3.0)]

    total = await async_import_hourly_statistics(hass, USAGE_ID, hourly)
    await async_wait_recording_done(hass)

    assert total == pytest.approx(6.0)
    rows = await _stored_rows(hass)
    assert [row["start"] for row in rows] == [h.timestamp() for h, _ in hourly]
    assert [row["sum"] for row in rows] == pytest.approx([1.0, 3.0, 6.0])
