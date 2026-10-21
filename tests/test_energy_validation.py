"""What Home Assistant's Energy dashboard and statistics validators report for the imported series."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.energy import data as energy_data
from homeassistant.components.energy.validate import async_validate
from homeassistant.components.recorder.statistics import validate_statistics
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.esb_smart_meter.const import DOMAIN
from custom_components.esb_smart_meter.models import ESBData
from custom_components.esb_smart_meter.tariff import TariffPeriod
from custom_components.esb_smart_meter.tariff_flow import PRESETS

MPRN = "12345678901"
USAGE_COST = f"esb_smart_meter:usage_cost_{MPRN}"
EXPORT_CREDIT = f"esb_smart_meter:export_credit_{MPRN}"


def _download() -> ESBData:
    end = datetime.now().replace(minute=0, second=0, microsecond=0) - timedelta(days=1)
    rows = []
    for i in range(96):
        label = (end - timedelta(minutes=30 * i)).strftime("%d-%m-%Y %H:%M")
        rows.append({"Read Date and End Time": label, "Read Value": "0.5", "Read Type": "Active Import Interval (kWh)"})
        rows.append({"Read Date and End Time": label, "Read Value": "0.1", "Read Type": "Active Export Interval (kWh)"})
    return ESBData(data=rows)


@pytest.fixture
async def configured(recorder_mock, hass, enable_custom_integrations):
    """Integration set up with a tariff, Energy dashboard pointed at its statistics."""
    hass.config.currency = "EUR"
    tariff = TariffPeriod.from_data({**PRESETS["sse_airtricity_smart"], "effective_from": "2020-01-01", "export_rate": 0.2})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MPRN,
        data={"username": "u", "password": "p", "mprn": MPRN},
        subentries_data=[ConfigSubentryData(data=tariff.as_data(), subentry_type="tariff", title="t", unique_id="t")],
    )
    entry.add_to_hass(hass)
    session = MagicMock(closed=False, close=AsyncMock())
    with patch("custom_components.esb_smart_meter.create_esb_session", AsyncMock(return_value=session)), patch(
        "custom_components.esb_smart_meter.ESBDataApi.fetch", AsyncMock(return_value=_download())
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        await async_wait_recording_done(hass)

    assert await async_setup_component(hass, "energy", {})
    manager = await energy_data.async_get_manager(hass)
    await manager.async_update(
        {
            "energy_sources": [
                {
                    "type": "grid",
                    "stat_energy_from": "sensor.esb_electricity_usage_history",
                    "stat_energy_to": "sensor.esb_electricity_export_history",
                    "stat_cost": USAGE_COST,
                    "entity_energy_price": None,
                    "number_energy_price": None,
                    "stat_compensation": EXPORT_CREDIT,
                    "entity_energy_price_export": None,
                    "number_energy_price_export": None,
                    "cost_adjustment_day": 0,
                }
            ]
        }
    )
    return hass


def _flagged(validation) -> set[str]:
    return {
        affected[0]
        for source in validation.as_dict()["energy_sources"]
        for issue in source
        for affected in issue["affected_entities"]
    }


@pytest.mark.asyncio
async def test_energy_dashboard_accepts_cost_statistics(configured):
    """External cost statistics raise no Energy dashboard issues."""
    flagged = _flagged(await async_validate(configured))

    assert USAGE_COST not in flagged
    assert EXPORT_CREDIT not in flagged
    # The kWh History series are still entity-based (stateless entities); migrating them is a separate change
    assert flagged <= {"sensor.esb_electricity_usage_history", "sensor.esb_electricity_export_history"}


@pytest.mark.asyncio
async def test_statistics_page_reports_no_issues(configured):
    """Developer tools -> Statistics offers no 'fix' (delete) for any imported series."""
    assert await configured.async_add_executor_job(validate_statistics, configured) == {}
