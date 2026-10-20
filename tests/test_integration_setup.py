"""End-to-end setup on a real Home Assistant instance (ESB transport stubbed)."""

from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import homeassistant.util.dt as dt_util
import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState, ConfigSubentryData
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.esb_smart_meter.api_client import InvalidAuthException
from custom_components.esb_smart_meter.const import CONF_MPRN, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from custom_components.esb_smart_meter.models import DUBLIN_TZ, ESBData

MPRN = "12345678901"


def _esb_data() -> ESBData:
    hour = (datetime.now() - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    return ESBData(
        data=[
            {
                "Read Date and End Time": (hour + timedelta(minutes=30)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "0.5",
                "Read Type": "Active Import Interval (kWh)",
            }
        ]
    )


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MPRN,
        title=f"ESB Smart Meter ({MPRN})",
        data={CONF_USERNAME: "u@example.com", CONF_PASSWORD: "pw", CONF_MPRN: MPRN},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def session():
    session = MagicMock()
    session.closed = False
    session.close = AsyncMock()
    with patch("custom_components.esb_smart_meter.create_esb_session", AsyncMock(return_value=session)):
        yield session


async def _setup(hass, entry, fetch):
    with patch("custom_components.esb_smart_meter.ESBDataApi.fetch", fetch):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_setup_names_entities_and_unloads(recorder_mock, hass, enable_custom_integrations, entry, session):
    await _setup(hass, entry, AsyncMock(return_value=_esb_data()))
    assert entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    entities = {e.unique_id: e for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert len(entities) == 22

    today = entities[f"{MPRN}_today"]
    # New installs get the same documented entity ids existing installs already have
    assert today.entity_id == "sensor.esb_electricity_usage_today"
    assert entities[f"{MPRN}_usage_history"].entity_id == "sensor.esb_electricity_usage_history"
    assert entities[f"{MPRN}_circuit_breaker_status"].entity_id == "sensor.esb_smart_meter_circuit_breaker_status"
    assert hass.states.get(today.entity_id).attributes["friendly_name"] == f"ESB Smart Meter ({MPRN}) Usage today"
    # The reading is 3h old, so "today" is empty just after midnight; the 24h window always holds it
    last_24h = entities[f"{MPRN}_last_24_hours"]
    assert hass.states.get(last_24h.entity_id).state == "0.5"
    assert today.entity_category is None
    assert entities[f"{MPRN}_api_status"].entity_category is EntityCategory.DIAGNOSTIC

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_password_at_setup_starts_reauth(
    recorder_mock, hass, enable_custom_integrations, entry, session
):
    await _setup(hass, entry, AsyncMock(side_effect=InvalidAuthException("bad")))

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    session.close.assert_awaited_once()


def _ten_days_of_usage(days_ago: int = 1, days: int = 10) -> ESBData:
    end = datetime.now().replace(minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    rows = [
        {
            "Read Date and End Time": (end - timedelta(minutes=30 * i)).strftime("%d-%m-%Y %H:%M"),
            "Read Value": "0.5",
            "Read Type": "Active Import Interval (kWh)",
        }
        for i in range(days * 48)
    ]
    return ESBData(data=rows)


TARIFF = {
    "effective_from": "2020-01-01", "day_rate": 0.30, "night_rate": 0.30, "peak_rate": 0.30,
    "standing_charge": 0.0, "export_rate": 0.0, "night_start": 23, "night_end": 8,
    "peak_start": 17, "peak_end": 19, "peak_weekdays_only": False,
}


async def _sums(hass, statistic_id):
    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        statistics_during_period, hass, dt_util.utcnow() - timedelta(days=30), None, {statistic_id}, "hour", None, {"sum"}
    )
    return [row["sum"] for row in rows.get(statistic_id, [])]


@pytest.mark.asyncio
async def test_backfill_cost_and_recost_after_tariff_change(recorder_mock, hass, enable_custom_integrations, session):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MPRN,
        data={CONF_USERNAME: "u@example.com", CONF_PASSWORD: "pw", CONF_MPRN: MPRN},
        subentries_data=[ConfigSubentryData(data=TARIFF, subentry_type="tariff", title="t", unique_id="2020-01-01")],
    )
    entry.add_to_hass(hass)
    fetch = AsyncMock(return_value=_ten_days_of_usage())

    with patch("custom_components.esb_smart_meter.ESBDataApi.fetch", fetch):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        usage = await _sums(hass, "sensor.esb_electricity_usage_history")
        cost = await _sums(hass, "sensor.esb_electricity_usage_cost_history")
        assert len(usage) == 240  # whole download backfilled
        assert usage[-1] == pytest.approx(240.0)  # 1 kWh per hour
        assert cost[-1] == pytest.approx(240 * 0.30)

        subentry = next(iter(entry.subentries.values()))
        hass.config_entries.async_update_subentry(entry, subentry, data={**TARIFF, "day_rate": 0.5, "night_rate": 0.5, "peak_rate": 0.5})
        await hass.async_block_till_done()  # update listener re-imports from the data in memory

        cost = await _sums(hass, "sensor.esb_electricity_usage_cost_history")
        assert cost[-1] == pytest.approx(240 * 0.5)  # all history re-costed
        assert await _sums(hass, "sensor.esb_electricity_usage_history") == usage  # kWh untouched
        fetch.assert_awaited_once()  # no re-download on a tariff change


@pytest.mark.asyncio
async def test_refresh_after_backfill_imports_recent_days(recorder_mock, hass, enable_custom_integrations, entry, session):
    with patch("custom_components.esb_smart_meter.ESBDataApi.fetch", AsyncMock(return_value=_ten_days_of_usage())):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)

        with patch(
            "custom_components.esb_smart_meter.sensor.async_import_hourly_statistics", new=AsyncMock(return_value=1.0)
        ) as importer:
            await entry.runtime_data.coordinator.async_refresh()
            await hass.async_block_till_done()

    usage_calls = [c for c in importer.call_args_list if c.args[1] == "sensor.esb_electricity_usage_history"]
    hours = usage_calls[-1].args[2]
    assert hours[-1][0] - hours[0][0] == timedelta(days=7)


@pytest.mark.asyncio
async def test_refresh_after_long_outage_fills_the_gap(recorder_mock, hass, enable_custom_integrations, entry, session):
    """A download with no overlap in the 7-day window still continues from the last stored hour."""
    fetch = AsyncMock(return_value=_ten_days_of_usage(days_ago=11))
    with patch("custom_components.esb_smart_meter.ESBDataApi.fetch", fetch):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)

        # ESB returns the whole history, now ten days newer: more than the 7-day window
        fetch.return_value = _ten_days_of_usage(days_ago=1, days=20)
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()

    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utcnow() - timedelta(days=30),
        None,
        {"sensor.esb_electricity_usage_history"},
        "hour",
        None,
        {"sum"},
    )
    starts = [row["start"] for row in rows["sensor.esb_electricity_usage_history"]]
    assert len(starts) == 480  # 20 days, nothing skipped
    assert all(b - a == 3600 for a, b in zip(starts, starts[1:]))
    assert rows["sensor.esb_electricity_usage_history"][-1]["sum"] == pytest.approx(480.0)


async def _rows(hass, statistic_id):
    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        statistics_during_period, hass, dt_util.utcnow() - timedelta(days=30), None, {statistic_id}, "hour", None, {"sum"}
    )
    return rows.get(statistic_id, [])


@pytest.mark.asyncio
async def test_moving_first_period_later_zeroes_costs_before_it(
    recorder_mock, hass, enable_custom_integrations, session
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MPRN,
        data={CONF_USERNAME: "u@example.com", CONF_PASSWORD: "pw", CONF_MPRN: MPRN},
        subentries_data=[ConfigSubentryData(data=TARIFF, subentry_type="tariff", title="t", unique_id="2020-01-01")],
    )
    entry.add_to_hass(hass)
    with patch("custom_components.esb_smart_meter.ESBDataApi.fetch", AsyncMock(return_value=_ten_days_of_usage())):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        usage = await _sums(hass, "sensor.esb_electricity_usage_history")
        assert (await _sums(hass, "sensor.esb_electricity_usage_cost_history"))[-1] == pytest.approx(240 * 0.30)

        new_start = (datetime.now(DUBLIN_TZ) - timedelta(days=6)).date()
        subentry = next(iter(entry.subentries.values()))
        hass.config_entries.async_update_subentry(entry, subentry, data={**TARIFF, "effective_from": new_start.isoformat()})
        await hass.async_block_till_done()

        cutoff = datetime.combine(new_start, time(), DUBLIN_TZ).timestamp()
        cost = await _rows(hass, "sensor.esb_electricity_usage_cost_history")
        before = [row["sum"] for row in cost if row["start"] < cutoff]
        after = [row for row in cost if row["start"] >= cutoff]
        assert len(before) + len(after) == 240
        assert before and all(total == pytest.approx(0.0) for total in before)  # baseline: nothing costed
        assert cost[-1]["sum"] == pytest.approx(len(after) * 0.30)
        assert await _sums(hass, "sensor.esb_electricity_usage_history") == usage  # kWh unchanged
