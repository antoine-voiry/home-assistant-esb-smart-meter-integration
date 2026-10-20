"""Tests for invalid-credential handling and the reauth flow."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esb_smart_meter.api_client import ESBDataApi, InvalidAuthException
from custom_components.esb_smart_meter.const import CONF_MPRN, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from custom_components.esb_smart_meter.coordinator import ESBDataUpdateCoordinator
from tests.conftest import _async_create_task_handler

MPRN = "12345678901"
SETTINGS_PAGE = '<html><script>var SETTINGS = {"csrf":"c","transId":"t"};</script></html>'


def _response(text: str) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(
        return_value=MagicMock(
            status=200,
            text=AsyncMock(return_value=text),
            raise_for_status=MagicMock(),
            url="https://login.esb.ie",
            headers={},
        )
    )
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


@pytest.fixture
def esb_api():
    """ESBDataApi with mocked transport and no cached session."""
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
    hass.async_create_task = MagicMock(side_effect=_async_create_task_handler)
    session = MagicMock()
    session.cookie_jar = []
    api = ESBDataApi(hass=hass, session=session, username="u@example.com", password="bad", mprn=MPRN)
    api._session_manager.load_session = AsyncMock(return_value=None)
    return api


@pytest.mark.asyncio
async def test_rejected_credentials_raise_invalid_auth(esb_api):
    """ESB answers a wrong password with a 200 JSON error body."""
    esb_api._session.get = MagicMock(return_value=_response(SETTINGS_PAGE))
    esb_api._session.post = MagicMock(
        return_value=_response(json.dumps({"status": "400", "message": "The email or password is not correct."}))
    )

    with pytest.raises(InvalidAuthException):
        await esb_api._ESBDataApi__login()


@pytest.mark.asyncio
async def test_invalid_auth_does_not_trip_circuit_breaker(esb_api):
    """Bad credentials need user action, not back-off."""
    with patch.object(esb_api, "_ESBDataApi__login", side_effect=InvalidAuthException("bad")):
        with pytest.raises(InvalidAuthException):
            await esb_api.fetch()
    assert esb_api._circuit_breaker._failure_count == 0


@pytest.mark.asyncio
async def test_coordinator_raises_auth_failed(hass):
    """The coordinator turns rejected credentials into ConfigEntryAuthFailed (starts reauth)."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MPRN: MPRN})
    entry.add_to_hass(hass)
    api = MagicMock()
    api.fetch = AsyncMock(side_effect=InvalidAuthException("bad"))
    coordinator = ESBDataUpdateCoordinator(hass=hass, esb_api=api, mprn=MPRN, config_entry=entry)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.fixture
def config_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MPRN,
        title=f"ESB Smart Meter ({MPRN})",
        data={CONF_USERNAME: "u@example.com", CONF_PASSWORD: "old", CONF_MPRN: MPRN},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_reauth_flow_updates_password(recorder_mock, hass, enable_custom_integrations, config_entry):
    """Reauth asks for the new password, stores it and reloads the entry."""
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["username"] == "u@example.com"

    with patch("custom_components.esb_smart_meter.async_setup_entry", return_value=True) as setup:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new"})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new"
    assert config_entry.data[CONF_USERNAME] == "u@example.com"
    assert setup.called


@pytest.mark.asyncio
async def test_reauth_rejects_empty_password(recorder_mock, hass, enable_custom_integrations, config_entry):
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "  "})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PASSWORD: "empty_password"}
    assert config_entry.data[CONF_PASSWORD] == "old"


@pytest.mark.asyncio
async def test_reauth_of_loaded_entry_reloads_once_via_listener(
    recorder_mock, hass, enable_custom_integrations, config_entry
):
    from custom_components.esb_smart_meter import _async_reload_on_update, _settings_snapshot
    from custom_components.esb_smart_meter.coordinator import ESBRuntimeData

    async def fake_setup(hass, entry):
        entry.runtime_data = ESBRuntimeData(
            coordinator=MagicMock(), session=MagicMock(), settings=_settings_snapshot(entry)
        )
        entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))
        return True

    with patch("custom_components.esb_smart_meter.async_setup_entry", side_effect=fake_setup) as setup, patch(
        "custom_components.esb_smart_meter.async_unload_entry", return_value=True
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        result = await config_entry.start_reauth_flow(hass)
        await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new"})
        await hass.async_block_till_done()

    assert setup.call_count == 2
    assert config_entry.data[CONF_PASSWORD] == "new"
