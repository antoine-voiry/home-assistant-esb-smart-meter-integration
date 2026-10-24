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
    async_copy_statistics,
    async_import_hourly_statistics,
    async_stored_hours,
    async_wait_committed,
    recent_hours,
)

IMPORT = "Active Import Interval (kWh)"
USAGE_ID = "sensor.esb_electricity_usage_history"
EXTERNAL_ID = "esb_smart_meter:usage_cost_12345678901"


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


@pytest.mark.asyncio
async def test_stored_hours(recorder_mock, hass, hourly_usage):
    """Return the hour starts stored for a series within [start, end)."""
    first, last = hourly_usage[0][0], hourly_usage[-1][0]
    assert await async_stored_hours(hass, USAGE_ID, first, last + timedelta(hours=1)) == set()

    await async_import_hourly_statistics(hass, USAGE_ID, hourly_usage)
    await async_wait_recording_done(hass)

    assert await async_stored_hours(hass, USAGE_ID, first - timedelta(hours=5), last + timedelta(hours=1)) == {
        hour for hour, _ in hourly_usage
    }
    assert await async_stored_hours(hass, USAGE_ID, last, last + timedelta(hours=1)) == {last}


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


def test_recent_hours_resumes_from_last_stored_hour_when_older():
    newest = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    hourly = [(newest - timedelta(hours=h), 1.0) for h in range(400, -1, -1)]
    resume = newest - timedelta(days=12)

    recent = recent_hours(hourly, days=7, resume_from=resume)

    assert recent[0][0] == resume
    assert recent[-1][0] == newest
    assert len(recent) == 12 * 24 + 1


def test_recent_hours_keeps_window_when_resume_is_newer():
    newest = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    hourly = [(newest - timedelta(hours=h), 1.0) for h in range(400, -1, -1)]

    recent = recent_hours(hourly, days=7, resume_from=newest + timedelta(hours=1))

    assert recent[0][0] == newest - timedelta(days=7)
    assert len(recent) == 7 * 24 + 1


@pytest.mark.asyncio
async def test_external_statistic_metadata(hourly_usage):
    """A domain:object_id statistic is added as an external statistic named for the picker."""
    hass = MagicMock()
    with patch("custom_components.esb_smart_meter.statistics.get_instance") as get_instance, patch(
        "custom_components.esb_smart_meter.statistics.async_add_external_statistics"
    ) as add_external, patch("custom_components.esb_smart_meter.statistics.async_import_statistics") as add:
        get_instance.return_value.async_add_executor_job = AsyncMock(return_value={})
        await async_import_hourly_statistics(
            hass, EXTERNAL_ID, hourly_usage, unit="EUR", name="ESB Smart Meter (12345678901) Usage cost"
        )

    add.assert_not_called()
    _hass, metadata, stats = add_external.call_args.args
    assert metadata["statistic_id"] == EXTERNAL_ID
    assert metadata["source"] == "esb_smart_meter"
    assert metadata["name"] == "ESB Smart Meter (12345678901) Usage cost"
    assert metadata["unit_of_measurement"] == "EUR"
    assert metadata["unit_class"] is None
    assert [s["sum"] for s in stats] == pytest.approx([0.3, 0.7])


@pytest.mark.asyncio
async def test_external_statistics_round_trip(recorder_mock, hass, hourly_usage):
    """External rows are stored, read back and used as the baseline like entity rows."""
    await async_import_hourly_statistics(hass, EXTERNAL_ID, hourly_usage, unit="EUR", name="cost")
    await async_wait_recording_done(hass)
    later = [(hour + timedelta(hours=2), value) for hour, value in hourly_usage]
    total = await async_import_hourly_statistics(hass, EXTERNAL_ID, later[-1:], unit="EUR", name="cost")
    await async_wait_recording_done(hass)

    start = hourly_usage[0][0]
    stored = await async_stored_hours(hass, EXTERNAL_ID, start, start + timedelta(hours=10))
    assert stored == {hour for hour, _ in hourly_usage} | {later[-1][0]}
    assert total == pytest.approx(0.7 + 0.4)  # baseline from the stored external rows


@pytest.mark.asyncio
async def test_wait_committed_returns_once_import_is_readable(recorder_mock, hass, hourly_usage):
    """After the wait, the next import's baseline query sees the rows queued before it."""
    total = await async_import_hourly_statistics(hass, USAGE_ID, hourly_usage)

    assert await async_wait_committed(hass, USAGE_ID, hourly_usage[-1][0], total) is True

    first, last = hourly_usage[0][0], hourly_usage[-1][0]
    assert await async_stored_hours(hass, USAGE_ID, first, last + timedelta(hours=1)) == {h for h, _ in hourly_usage}


@pytest.mark.asyncio
async def test_wait_committed_gives_up_and_warns(hourly_usage, caplog):
    """A recorder that never shows the row must not hang the import forever."""
    hass = MagicMock()
    with patch("custom_components.esb_smart_meter.statistics.get_instance") as get_instance:
        get_instance.return_value.async_add_executor_job = AsyncMock(
            return_value={USAGE_ID: [{"start": 0.0, "sum": 1.0}]}  # an older sum, not the imported one
        )
        committed = await async_wait_committed(hass, USAGE_ID, hourly_usage[-1][0], 0.7, attempts=3)

    assert committed is False
    assert get_instance.return_value.async_add_executor_job.await_count == 3
    assert "not readable" in caplog.text


@pytest.mark.asyncio
async def test_copy_statistics_moves_full_history_to_external(recorder_mock, hass, hourly_usage):
    """Every stored row, including ones older than any download, is copied with its sums."""
    await async_import_hourly_statistics(hass, USAGE_ID, hourly_usage)
    await async_wait_recording_done(hass)

    copied = await async_copy_statistics(hass, USAGE_ID, EXTERNAL_ID, unit="kWh", name="Usage")

    assert copied == len(hourly_usage)
    first, last = hourly_usage[0][0], hourly_usage[-1][0]
    assert await async_stored_hours(hass, EXTERNAL_ID, first, last + timedelta(hours=1)) == {h for h, _ in hourly_usage}
    rows = await hass.async_add_executor_job(
        statistics_during_period, hass, first, None, {EXTERNAL_ID}, "hour", None, {"state", "sum"}
    )
    assert [row["sum"] for row in rows[EXTERNAL_ID]] == pytest.approx([0.3, 0.7])
    # The source is untouched; deleting it is left to the user
    assert await async_stored_hours(hass, USAGE_ID, first, last + timedelta(hours=1)) == {h for h, _ in hourly_usage}


@pytest.mark.asyncio
async def test_copy_statistics_without_source_rows_copies_nothing(recorder_mock, hass):
    assert await async_copy_statistics(hass, USAGE_ID, EXTERNAL_ID, unit="kWh", name="Usage") == 0


@pytest.mark.asyncio
async def test_copy_statistics_reports_unreadable_copy(recorder_mock, hass, hourly_usage):
    await async_import_hourly_statistics(hass, USAGE_ID, hourly_usage)
    await async_wait_recording_done(hass)
    with patch("custom_components.esb_smart_meter.statistics.async_wait_committed", AsyncMock(return_value=False)):
        assert await async_copy_statistics(hass, USAGE_ID, EXTERNAL_ID, unit="kWh", name="Usage") is None


async def _stored_rows(hass, statistic_id=USAGE_ID):
    """Read actual stored state/sum/change, including the preceding baseline."""
    stats = await hass.async_add_executor_job(
        statistics_during_period, hass, datetime(2026, 4, 1, tzinfo=timezone.utc), None,
        {statistic_id}, "hour", None, {"state", "sum", "change"}
    )
    return stats.get(statistic_id, [])


@pytest.mark.asyncio
async def test_first_import_publishes_only_complete_hours(recorder_mock, hass):
    """The lone leading half is left out; the trailing half waits for the next refresh."""
    h = datetime(2026, 4, 16, 13)  # Dublin: one hour ahead of UTC
    rows = [_row(h + timedelta(minutes=30 * i), value, IMPORT)
            for i, value in enumerate([0.25, 0.5, 0.5, 1, 2, 0.9])]
    assert await async_import_hourly_statistics(hass, USAGE_ID, ESBData(data=rows).hourly_usage()) == 4
    await async_wait_recording_done(hass)
    stats = await _stored_rows(hass)
    assert [row["start"] for row in stats] == [
        datetime(2026, 4, 16, hour, tzinfo=timezone.utc).timestamp() for hour in (12, 13)
    ]
    assert [row["sum"] for row in stats] == [1, 4]
    rows.append(_row(h + timedelta(hours=3), 0.1, IMPORT))
    assert await async_import_hourly_statistics(hass, USAGE_ID, ESBData(data=rows).hourly_usage()) == 5
    await async_wait_recording_done(hass)
    assert [row["change"] for row in await _stored_rows(hass)] == pytest.approx([1, 3, 1])


@pytest.mark.asyncio
async def test_internal_hole_is_imported_not_deferred(recorder_mock, hass):
    """ESB gaps are permanent: an hour missing a half keeps the half that exists and the import goes ahead."""
    h = datetime(2026, 4, 16, 13)
    rows = [_row(h + timedelta(minutes=30 * i), value, IMPORT) for i, value in enumerate([0.5, 0.5, 1, 1, 1.5, 1.5], start=1)]
    hourly = ESBData(data=rows[:2] + rows[3:]).hourly_usage()  # One end of the middle hour never arrives.
    assert [kwh for _, kwh in hourly] == pytest.approx([1, 1, 3])

    assert await async_import_hourly_statistics(hass, USAGE_ID, hourly) == pytest.approx(5)
    await async_wait_recording_done(hass)

    assert [row["sum"] for row in await _stored_rows(hass)] == pytest.approx([1, 2, 5])


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
