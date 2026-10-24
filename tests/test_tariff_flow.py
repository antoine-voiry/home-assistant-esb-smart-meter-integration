"""Tests for the tariff period subentry flow."""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esb_smart_meter.const import CONF_MPRN, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from custom_components.esb_smart_meter.coordinator import ESBRuntimeData

MPRN = "12345678901"
RATES = {
    "effective_from": "2026-10-05",
    "day_rate": 0.3381,
    "night_rate": 0.2056,
    "peak_rate": 0.3788,
    "standing_charge": 0.7671,
    "export_rate": 0.0,
    "night_start": 23,
    "night_end": 8,
    "peak_start": 17,
    "peak_end": 19,
    "peak_weekdays_only": False,
}


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MPRN,
        data={CONF_USERNAME: "u@example.com", CONF_PASSWORD: "pw", CONF_MPRN: MPRN},
        subentries_data=[
            ConfigSubentryData(
                data={**RATES, "effective_from": "2026-01-01"},
                subentry_type="tariff",
                title="Tariff from 2026-01-01",
                unique_id="2026-01-01",
            )
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def _start(hass, entry):
    return await hass.config_entries.subentries.async_init((entry.entry_id, "tariff"), context={"source": "user"})


@pytest.mark.asyncio
async def test_sse_preset_prefills_rates(recorder_mock, hass, enable_custom_integrations, entry):
    result = await _start(hass, entry)
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"preset": "sse_airtricity_smart"})

    assert result["step_id"] == "rates"
    suggested = {str(key): key.description["suggested_value"] for key in result["data_schema"].schema if key.description}
    assert suggested["day_rate"] == 0.3381
    assert suggested["peak_weekdays_only"] is False


@pytest.mark.asyncio
async def test_bord_gais_preset_peak_weekdays_only(recorder_mock, hass, enable_custom_integrations, entry):
    result = await _start(hass, entry)
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"preset": "bord_gais_smart"})
    suggested = {str(key): key.description["suggested_value"] for key in result["data_schema"].schema if key.description}
    assert suggested["peak_weekdays_only"] is True
    assert suggested["day_rate"] == 0.3491


@pytest.mark.asyncio
async def test_add_period_creates_subentry(recorder_mock, hass, enable_custom_integrations, entry):
    result = await _start(hass, entry)
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"preset": "custom"})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], RATES)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Tariff from 2026-10-05"
    created = [s for s in entry.subentries.values() if s.unique_id == "2026-10-05"]
    assert created and created[0].data["day_rate"] == 0.3381


@pytest.mark.asyncio
async def test_add_period_rejects_duplicate_date(recorder_mock, hass, enable_custom_integrations, entry):
    result = await _start(hass, entry)
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"preset": "custom"})
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**RATES, "effective_from": "2026-01-01"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"effective_from": "duplicate_period"}


@pytest.mark.asyncio
async def test_reconfigure_updates_period(recorder_mock, hass, enable_custom_integrations, entry):
    subentry_id = next(iter(entry.subentries))
    result = await entry.start_subentry_reconfigure_flow(hass, subentry_id)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**RATES, "effective_from": "2026-01-01", "day_rate": 0.4}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[subentry_id].data["day_rate"] == 0.4


@pytest.mark.asyncio
async def test_subentry_change_updates_sensors_without_reload(recorder_mock, hass, enable_custom_integrations, entry):
    coordinator = MagicMock()

    async def fake_setup(hass, entry):
        from custom_components.esb_smart_meter import _async_reload_on_update, _settings_snapshot

        entry.runtime_data = ESBRuntimeData(
            coordinator=coordinator, session=MagicMock(), settings=_settings_snapshot(entry)
        )
        entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))
        return True

    with patch("custom_components.esb_smart_meter.async_setup_entry", side_effect=fake_setup) as setup, patch(
        "custom_components.esb_smart_meter.async_unload_entry", return_value=True
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        result = await _start(hass, entry)
        result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"preset": "custom"})
        await hass.config_entries.subentries.async_configure(result["flow_id"], RATES)
        await hass.async_block_till_done()

    assert len(entry.subentries) == 2
    assert setup.call_count == 1  # no reload, so no new ESB login or download
    coordinator.async_update_listeners.assert_called_once_with()
