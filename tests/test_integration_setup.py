"""End-to-end setup on a real Home Assistant instance (ESB transport stubbed)."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esb_smart_meter.api_client import InvalidAuthException
from custom_components.esb_smart_meter.const import CONF_MPRN, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from custom_components.esb_smart_meter.models import ESBData

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
    assert len(entities) == 20

    today = entities[f"{MPRN}_today"]
    # New installs get the same documented entity ids existing installs already have
    assert today.entity_id == "sensor.esb_electricity_usage_today"
    assert entities[f"{MPRN}_usage_history"].entity_id == "sensor.esb_electricity_usage_history"
    assert entities[f"{MPRN}_circuit_breaker_status"].entity_id == "sensor.esb_smart_meter_circuit_breaker_status"
    assert hass.states.get(today.entity_id).attributes["friendly_name"] == f"ESB Smart Meter ({MPRN}) Usage today"
    assert hass.states.get(today.entity_id).state == "0.5"
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
