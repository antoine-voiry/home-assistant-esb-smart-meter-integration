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
COST_ID = f"esb_smart_meter:usage_cost_{MPRN}"
USAGE_ID = f"esb_smart_meter:usage_{MPRN}"


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
        await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.asyncio
async def test_setup_names_entities_and_unloads(recorder_mock, hass, enable_custom_integrations, entry, session):
    await _setup(hass, entry, AsyncMock(return_value=_esb_data()))
    assert entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    entities = {e.unique_id: e for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert len(entities) == 20

    today = entities[f"{MPRN}_today"]
    # New installs get the same documented entity ids existing installs already have
    assert today.entity_id == "sensor.esb_electricity_usage_today"
    assert entities[f"{MPRN}_usage_total"].entity_id == "sensor.esb_electricity_usage_total"
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
        await hass.async_block_till_done(wait_background_tasks=True)

        usage = await _sums(hass, USAGE_ID)
        cost = await _sums(hass, COST_ID)
        assert len(usage) == 240  # whole download backfilled
        assert usage[-1] == pytest.approx(240.0)  # 1 kWh per hour
        assert cost[-1] == pytest.approx(240 * 0.30)

        subentry = next(iter(entry.subentries.values()))
        hass.config_entries.async_update_subentry(entry, subentry, data={**TARIFF, "day_rate": 0.5, "night_rate": 0.5, "peak_rate": 0.5})
        await hass.async_block_till_done(wait_background_tasks=True)  # update listener re-imports from the data in memory

        cost = await _sums(hass, COST_ID)
        assert cost[-1] == pytest.approx(240 * 0.5)  # all history re-costed
        assert await _sums(hass, USAGE_ID) == usage  # kWh untouched
        fetch.assert_awaited_once()  # no re-download on a tariff change


@pytest.mark.asyncio
async def test_refresh_after_backfill_imports_recent_days(recorder_mock, hass, enable_custom_integrations, entry, session):
    with patch("custom_components.esb_smart_meter.ESBDataApi.fetch", AsyncMock(return_value=_ten_days_of_usage())):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        await async_wait_recording_done(hass)

        with patch(
            "custom_components.esb_smart_meter.sensor.async_import_hourly_statistics", new=AsyncMock(return_value=1.0)
        ) as importer:
            await entry.runtime_data.coordinator.async_refresh()
            await hass.async_block_till_done(wait_background_tasks=True)

    usage_calls = [c for c in importer.call_args_list if c.args[1] == USAGE_ID]
    hours = usage_calls[-1].args[2]
    assert hours[-1][0] - hours[0][0] == timedelta(days=7)


@pytest.mark.asyncio
async def test_refresh_after_long_outage_fills_the_gap(recorder_mock, hass, enable_custom_integrations, entry, session):
    """A download with no overlap in the 7-day window still continues from the last stored hour."""
    fetch = AsyncMock(return_value=_ten_days_of_usage(days_ago=11))
    with patch("custom_components.esb_smart_meter.ESBDataApi.fetch", fetch):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        await async_wait_recording_done(hass)

        # ESB returns the whole history, now ten days newer: more than the 7-day window
        fetch.return_value = _ten_days_of_usage(days_ago=1, days=20)
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done(wait_background_tasks=True)

    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utcnow() - timedelta(days=30),
        None,
        {USAGE_ID},
        "hour",
        None,
        {"sum"},
    )
    starts = [row["start"] for row in rows[USAGE_ID]]
    assert len(starts) == 480  # 20 days, nothing skipped
    assert all(b - a == 3600 for a, b in zip(starts, starts[1:]))
    assert rows[USAGE_ID][-1]["sum"] == pytest.approx(480.0)


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
        await hass.async_block_till_done(wait_background_tasks=True)
        usage = await _sums(hass, USAGE_ID)
        assert (await _sums(hass, COST_ID))[-1] == pytest.approx(240 * 0.30)

        new_start = (datetime.now(DUBLIN_TZ) - timedelta(days=6)).date()
        subentry = next(iter(entry.subentries.values()))
        hass.config_entries.async_update_subentry(entry, subentry, data={**TARIFF, "effective_from": new_start.isoformat()})
        await hass.async_block_till_done(wait_background_tasks=True)

        cutoff = datetime.combine(new_start, time(), DUBLIN_TZ).timestamp()
        cost = await _rows(hass, COST_ID)
        before = [row["sum"] for row in cost if row["start"] < cutoff]
        after = [row for row in cost if row["start"] >= cutoff]
        assert len(before) + len(after) == 240
        assert before and all(total == pytest.approx(0.0) for total in before)  # baseline: nothing costed
        assert cost[-1]["sum"] == pytest.approx(len(after) * 0.30)
        assert await _sums(hass, USAGE_ID) == usage  # kWh unchanged


@pytest.mark.asyncio
async def test_gap_filled_in_late_by_esb_is_imported(recorder_mock, hass, enable_custom_integrations, entry, session):
    """ESB filling in hours older than the recent window (meter back online) must reach the statistics."""
    complete = _ten_days_of_usage()
    hours = complete.hourly_usage()
    gap = {hour for hour, _ in hours[48:96]}  # two days, more than 7 days before the newest hour
    with_gap = ESBData(
        data=[
            {
                "Read Date and End Time": (end.astimezone(DUBLIN_TZ)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "0.5",
                "Read Type": "Active Import Interval (kWh)",
            }
            for start, _kwh in hours
            if start not in gap
            for end in (start + timedelta(minutes=30), start + timedelta(hours=1))
        ]
    )
    assert len(with_gap.hourly_usage()) == len(hours) - 48
    fetch = AsyncMock(return_value=with_gap)
    with patch("custom_components.esb_smart_meter.ESBDataApi.fetch", fetch):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        await async_wait_recording_done(hass)

        fetch.return_value = complete
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done(wait_background_tasks=True)

    rows = await _rows(hass, USAGE_ID)
    assert len(rows) == len(hours)
    assert rows[-1]["sum"] == pytest.approx(sum(kwh for _, kwh in hours))


@pytest.mark.asyncio
async def test_upgrade_moves_history_and_energy_dashboard(recorder_mock, hass, enable_custom_integrations, entry, session):
    """An install from before external statistics keeps all its history, older than the download too."""
    from homeassistant.components.energy import data as energy_data
    from homeassistant.setup import async_setup_component

    from custom_components.esb_smart_meter.statistics import async_import_hourly_statistics

    download = _ten_days_of_usage()
    hours = download.hourly_usage()
    older = [(hours[0][0] - timedelta(days=5) + timedelta(hours=h), 0.25) for h in range(24)]  # beyond the download
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, f"{MPRN}_usage_history", config_entry=entry, suggested_object_id="esb_electricity_usage_history"
    )
    await async_import_hourly_statistics(hass, old.entity_id, older + hours)
    await async_wait_recording_done(hass)
    assert await async_setup_component(hass, "energy", {})
    manager = await energy_data.async_get_manager(hass)
    await manager.async_update(
        {
            "energy_sources": [
                {
                    "type": "grid",
                    "stat_energy_from": old.entity_id,
                    "stat_energy_to": None,
                    "stat_cost": None,
                    "entity_energy_price": None,
                    "number_energy_price": None,
                    "stat_compensation": None,
                    "entity_energy_price_export": None,
                    "number_energy_price_export": None,
                    "cost_adjustment_day": 0,
                }
            ]
        }
    )

    await _setup(hass, entry, AsyncMock(return_value=download))
    await async_wait_recording_done(hass)

    rows = await hass.async_add_executor_job(
        statistics_during_period, hass, older[0][0], None, {USAGE_ID}, "hour", None, {"sum"}
    )
    sums = [row["sum"] for row in rows[USAGE_ID]]
    expected_total = sum(kwh for _, kwh in older + hours)
    assert len(sums) == len(older) + len(hours)  # nothing lost, nothing duplicated
    assert sums[-1] == pytest.approx(expected_total)
    assert all(b >= a for a, b in zip(sums, sums[1:]))
    assert manager.data["energy_sources"][0]["stat_energy_from"] == USAGE_ID
    assert registry.async_get(old.entity_id) is None
    assert float(hass.states.get("sensor.esb_electricity_usage_total").state) == pytest.approx(expected_total)


@pytest.mark.asyncio
async def test_setup_flags_energy_dashboard_using_esb_entities(
    recorder_mock, hass, enable_custom_integrations, entry, session
):
    """An Energy dashboard pointed at an ESB period sensor gets a Repair issue on setup."""
    from homeassistant.components.energy import data as energy_data
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "energy", {})
    manager = await energy_data.async_get_manager(hass)
    await _setup(hass, entry, AsyncMock(return_value=_ten_days_of_usage()))
    await manager.async_update(
        {
            "energy_sources": [
                {
                    "type": "grid",
                    "stat_energy_from": "sensor.esb_electricity_usage_last_7_days",
                    "stat_energy_to": None,
                    "stat_cost": None,
                    "entity_energy_price": None,
                    "number_energy_price": None,
                    "stat_compensation": None,
                    "entity_energy_price_export": None,
                    "number_energy_price_export": None,
                    "cost_adjustment_day": 0,
                }
            ]
        }
    )

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"energy_dashboard_entities_{entry.entry_id}")
    assert issue is not None
    assert issue.translation_placeholders["entities"] == "sensor.esb_electricity_usage_last_7_days"
