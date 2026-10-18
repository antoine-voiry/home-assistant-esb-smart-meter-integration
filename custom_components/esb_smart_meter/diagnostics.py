"""Diagnostics support for ESB Smart Meter."""

from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_MANUAL_COOKIES, CONF_MPRN, CONF_PASSWORD, CONF_SESSION_COOKIES, CONF_USERNAME
from .coordinator import ESBConfigEntry
from .models import ESBData

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, CONF_MPRN, CONF_MANUAL_COOKIES, CONF_SESSION_COOKIES}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ESBConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry, with credentials and the MPRN redacted."""
    coordinator = entry.runtime_data.coordinator
    breaker = coordinator.esb_api._circuit_breaker
    last_update = coordinator.last_successful_update_time
    last_failure = breaker._last_failure_time

    return {
        "entry": {
            # The title and unique id both embed the MPRN
            "title": REDACTED,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_successful_update_time": last_update.isoformat() if last_update else None,
            "update_interval_hours": coordinator.update_interval.total_seconds() / 3600,
            "captcha_backoff_active": coordinator._captcha_notification_sent,
        },
        "circuit_breaker": {
            "is_open": breaker._is_open,
            "failure_count": breaker._failure_count,
            "daily_attempts": breaker._daily_attempts,
            "last_failure": last_failure.isoformat() if last_failure else None,
        },
        "data": _summarise(coordinator.data),
    }


def _summarise(data: ESBData | None) -> dict[str, Any] | None:
    """Describe the downloaded readings without including them."""
    if data is None:
        return None
    timestamps = [moment for moment, _ in data._data + data._export_data]
    return {
        "import_readings": len(data._data),
        "export_readings": len(data._export_data),
        "first_reading": min(timestamps).isoformat() if timestamps else None,
        "last_reading": max(timestamps).isoformat() if timestamps else None,
    }
