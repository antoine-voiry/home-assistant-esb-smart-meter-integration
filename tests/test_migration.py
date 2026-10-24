"""Moving the entity-based History statistics to external statistics."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.energy import data as energy_data
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.esb_smart_meter.migration import async_migrate_legacy_history
from custom_components.esb_smart_meter.statistics import async_import_hourly_statistics

MPRN = "12345678901"
LEGACY_UNIQUE_ID = f"{MPRN}_usage_history"
NEW_ID = f"esb_smart_meter:usage_{MPRN}"
START = datetime(2023, 1, 1, tzinfo=timezone.utc)  # older than any ESB download
HOURS = [(START + timedelta(hours=h), 0.5) for h in range(48)]


async def _legacy(hass, entity_id="sensor.esb_electricity_usage_history", rows=HOURS):
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "esb_smart_meter", LEGACY_UNIQUE_ID, suggested_object_id="x")
    if entry.entity_id != entity_id:
        registry.async_update_entity(entry.entity_id, new_entity_id=entity_id)
    if rows:
        await async_import_hourly_statistics(hass, entity_id, rows)
        await async_wait_recording_done(hass)
    return entity_id


async def _energy(hass, stat_energy_from):
    assert await async_setup_component(hass, "energy", {})
    manager = await energy_data.async_get_manager(hass)
    await manager.async_update(
        {
            "energy_sources": [
                {
                    "type": "grid",
                    "stat_energy_from": stat_energy_from,
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
    return manager


async def _sums(hass, statistic_id):
    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        statistics_during_period, hass, START, None, {statistic_id}, "hour", None, {"sum"}
    )
    return [row["sum"] for row in rows.get(statistic_id, [])]


async def _migrate(hass):
    await async_migrate_legacy_history(hass, LEGACY_UNIQUE_ID, NEW_ID, unit="kWh", name=f"ESB Smart Meter ({MPRN}) Usage")


@pytest.mark.asyncio
async def test_history_moves_to_external_statistic(recorder_mock, hass):
    old_id = await _legacy(hass)
    manager = await _energy(hass, old_id)

    await _migrate(hass)

    assert await _sums(hass, NEW_ID) == pytest.approx([0.5 * (h + 1) for h in range(48)])
    assert manager.data["energy_sources"][0]["stat_energy_from"] == NEW_ID
    assert er.async_get(hass).async_get(old_id) is None
    # Old rows are kept for the user to delete from Developer tools if they want
    assert len(await _sums(hass, old_id)) == 48


@pytest.mark.asyncio
async def test_renamed_legacy_entity_is_found_by_unique_id(recorder_mock, hass):
    old_id = await _legacy(hass, entity_id="sensor.my_electricity")

    await _migrate(hass)

    assert len(await _sums(hass, NEW_ID)) == 48
    assert er.async_get(hass).async_get(old_id) is None


@pytest.mark.asyncio
async def test_nothing_to_migrate_on_new_install(recorder_mock, hass):
    with patch("custom_components.esb_smart_meter.migration.async_copy_statistics", AsyncMock()) as copy:
        await _migrate(hass)
    copy.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_entity_without_statistics_is_just_removed(recorder_mock, hass):
    old_id = await _legacy(hass, rows=[])

    await _migrate(hass)

    assert er.async_get(hass).async_get(old_id) is None
    assert await _sums(hass, NEW_ID) == []


@pytest.mark.asyncio
async def test_failed_copy_keeps_entity_and_dashboard(recorder_mock, hass):
    """If the copy can't be confirmed, nothing is changed so the next start retries."""
    old_id = await _legacy(hass)
    manager = await _energy(hass, old_id)
    with patch("custom_components.esb_smart_meter.migration.async_copy_statistics", AsyncMock(return_value=None)):
        await _migrate(hass)

    assert er.async_get(hass).async_get(old_id) is not None
    assert manager.data["energy_sources"][0]["stat_energy_from"] == old_id


@pytest.mark.asyncio
async def test_without_energy_dashboard_still_migrates(recorder_mock, hass):
    old_id = await _legacy(hass)

    await _migrate(hass)

    assert "energy" not in hass.config.components
    assert er.async_get(hass).async_get(old_id) is None
