"""Repair issue when the Energy dashboard uses ESB entities instead of ESB statistics."""

import pytest
from homeassistant.components.energy import data as energy_data
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esb_smart_meter.const import CONF_MPRN, DOMAIN
from custom_components.esb_smart_meter.energy_check import (
    async_check_energy_dashboard,
    async_setup_energy_check,
)

MPRN = "10301831754"
USAGE = f"esb_smart_meter:usage_{MPRN}"
EXPORT = f"esb_smart_meter:export_{MPRN}"


def _grid(stat_energy_from, stat_energy_to=None, stat_cost=None):
    return {
        "type": "grid",
        "stat_energy_from": stat_energy_from,
        "stat_energy_to": stat_energy_to,
        "stat_cost": stat_cost,
        "entity_energy_price": None,
        "number_energy_price": None,
        "stat_compensation": None,
        "entity_energy_price_export": None,
        "number_energy_price_export": None,
        "cost_adjustment_day": 0,
    }


@pytest.fixture
async def setup(recorder_mock, hass):
    """An ESB entry with a period sensor and an orphan from an older version, plus the energy integration."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MPRN, data={CONF_MPRN: MPRN})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    period = registry.async_get_or_create(
        "sensor", DOMAIN, f"{MPRN}_last_24_hours", config_entry=entry, suggested_object_id="esb_electricity_usage_last_24_hours"
    )
    orphan = registry.async_get_or_create(
        "sensor", DOMAIN, f"{MPRN}_total", config_entry=entry, suggested_object_id="first_floor_usage_total"
    )
    assert await async_setup_component(hass, "energy", {})
    manager = await energy_data.async_get_manager(hass)
    return hass, entry, manager, period.entity_id, orphan.entity_id


def _issue(hass, entry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"energy_dashboard_entities_{entry.entry_id}")


@pytest.mark.asyncio
async def test_esb_entities_in_energy_dashboard_raise_issue(setup):
    hass, entry, manager, period, orphan = setup
    # HA derives "<entity>_cost" sensors from a price; one built on an ESB period sensor is just as wrong
    await manager.async_update(
        {"energy_sources": [_grid(orphan, EXPORT, f"{period}_cost"), _grid("sensor.esb_electricity_usage_last_24_hours")]}
    )

    await async_check_energy_dashboard(hass)

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "energy_dashboard_entities"
    placeholders = issue.translation_placeholders
    assert placeholders["entities"] == ", ".join(sorted({orphan, period, f"{period}_cost"}))
    assert placeholders["usage"] == f"ESB Smart Meter ({MPRN}) Usage"
    assert placeholders["export"] == f"ESB Smart Meter ({MPRN}) Export"
    assert placeholders["usage_cost"] == f"ESB Smart Meter ({MPRN}) Usage cost"
    assert placeholders["export_credit"] == f"ESB Smart Meter ({MPRN}) Export credit"


@pytest.mark.asyncio
async def test_esb_statistics_raise_no_issue(setup):
    hass, entry, manager, *_ = setup
    await manager.async_update({"energy_sources": [_grid(USAGE, EXPORT, f"esb_smart_meter:usage_cost_{MPRN}")]})

    await async_check_energy_dashboard(hass)

    assert _issue(hass, entry) is None


@pytest.mark.asyncio
async def test_other_integrations_are_not_flagged(setup):
    hass, entry, manager, *_ = setup
    other = er.async_get(hass).async_get_or_create("sensor", "other", "meter", suggested_object_id="other_meter")
    await manager.async_update({"energy_sources": [_grid(other.entity_id, None, f"{other.entity_id}_cost")]})

    await async_check_energy_dashboard(hass)

    assert _issue(hass, entry) is None


@pytest.mark.asyncio
async def test_issue_follows_energy_settings(setup):
    """Registered once; fixing the dashboard clears the issue, breaking it again raises it."""
    hass, entry, manager, period, _orphan = setup
    listeners = len(manager._update_listeners)  # the energy integration has its own
    await async_setup_energy_check(hass)
    await async_setup_energy_check(hass)  # a reload must not add a second listener
    assert len(manager._update_listeners) == listeners + 1

    await manager.async_update({"energy_sources": [_grid(period)]})
    assert _issue(hass, entry) is not None

    await manager.async_update({"energy_sources": [_grid(USAGE)]})
    assert _issue(hass, entry) is None


@pytest.mark.asyncio
async def test_check_without_energy_integration_does_nothing(recorder_mock, hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MPRN, data={CONF_MPRN: MPRN})
    entry.add_to_hass(hass)

    await async_setup_energy_check(hass)
    await async_check_energy_dashboard(hass)

    assert "energy" not in hass.config.components
    assert _issue(hass, entry) is None


@pytest.mark.asyncio
async def test_empty_energy_settings_raise_no_issue(setup):
    hass, entry, *_ = setup
    await async_check_energy_dashboard(hass)
    assert _issue(hass, entry) is None
