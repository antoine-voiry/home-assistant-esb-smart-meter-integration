"""Tests for the config entry diagnostics download."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from homeassistant.components.diagnostics import REDACTED
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esb_smart_meter.const import (
    CONF_MANUAL_COOKIES,
    CONF_MPRN,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.esb_smart_meter.coordinator import ESBRuntimeData
from custom_components.esb_smart_meter.diagnostics import async_get_config_entry_diagnostics
from custom_components.esb_smart_meter.models import ESBData

MPRN = "12345678901"
LAST_UPDATE = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


def _row(dt, value, read_type):
    return {"Read Date and End Time": dt.strftime("%d-%m-%Y %H:%M"), "Read Value": str(value), "Read Type": read_type}


def _coordinator(data):
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = True
    coordinator.last_successful_update_time = LAST_UPDATE
    coordinator.update_interval = timedelta(hours=24)
    coordinator._captcha_notification_sent = False
    breaker = coordinator.esb_api._circuit_breaker
    breaker._is_open = False
    breaker._failure_count = 1
    breaker._daily_attempts = 2
    breaker._last_failure_time = None
    return coordinator


@pytest.fixture
def entry():
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MPRN,
        title=f"ESB Smart Meter ({MPRN})",
        data={CONF_USERNAME: "u@example.com", CONF_PASSWORD: "secret", CONF_MPRN: MPRN},
        options={CONF_UPDATE_INTERVAL: 12, CONF_MANUAL_COOKIES: "session=abc"},
    )
    return entry


@pytest.mark.asyncio
async def test_diagnostics_redact_secrets(hass, entry):
    """No credential, meter number or cookie may appear anywhere in the download."""
    entry.runtime_data = ESBRuntimeData(coordinator=_coordinator(None), session=MagicMock())

    result = await async_get_config_entry_diagnostics(hass, entry)

    dumped = json.dumps(result, default=str)
    for secret in ("u@example.com", "secret", MPRN, "session=abc"):
        assert secret not in dumped
    assert result["entry"]["data"][CONF_PASSWORD] == REDACTED
    assert result["entry"]["options"][CONF_UPDATE_INTERVAL] == 12


@pytest.mark.asyncio
async def test_diagnostics_report_coordinator_and_breaker(hass, entry):
    entry.runtime_data = ESBRuntimeData(coordinator=_coordinator(None), session=MagicMock())

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["coordinator"] == {
        "last_update_success": True,
        "last_successful_update_time": LAST_UPDATE.isoformat(),
        "update_interval_hours": 24.0,
        "captcha_backoff_active": False,
    }
    assert result["circuit_breaker"]["failure_count"] == 1
    assert result["circuit_breaker"]["daily_attempts"] == 2
    assert result["circuit_breaker"]["is_open"] is False
    assert result["data"] is None


@pytest.mark.asyncio
async def test_diagnostics_summarise_data(hass, entry):
    """Data is summarised (counts and range), not dumped row by row."""
    start = (datetime.now() - timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    rows = [
        _row(start + timedelta(minutes=30), 0.1, "Active Import Interval (kWh)"),
        _row(start + timedelta(minutes=60), 0.2, "Active Import Interval (kWh)"),
        _row(start + timedelta(minutes=60), 0.05, "Active Export Interval (kWh)"),
    ]
    entry.runtime_data = ESBRuntimeData(coordinator=_coordinator(ESBData(data=rows)), session=MagicMock())

    result = await async_get_config_entry_diagnostics(hass, entry)

    data = result["data"]
    assert data["import_readings"] == 2
    assert data["export_readings"] == 1
    assert data["first_reading"] < data["last_reading"]


@pytest.mark.asyncio
async def test_diagnostics_handle_empty_download(hass, entry):
    entry.runtime_data = ESBRuntimeData(coordinator=_coordinator(ESBData(data=[])), session=MagicMock())

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["data"] == {"import_readings": 0, "export_readings": 0, "first_reading": None, "last_reading": None}
