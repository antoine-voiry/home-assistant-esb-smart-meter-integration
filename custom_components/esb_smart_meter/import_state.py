"""Remembers which tariffs each cost series was last fully imported with."""

from datetime import datetime, timezone

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
COST_SERIES = ("usage_cost", "export_credit")


class ImportState:
    """Per-entry, per-series store of the last full import's tariff fingerprint and first costed hour."""

    def __init__(self, hass: HomeAssistant, entry_id: str, series: str) -> None:
        """Initialize the store for one series of one entry."""
        self._store: Store[dict[str, str | None]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.{series}")

    async def async_get_fingerprint(self) -> str | None:
        """Return the stored fingerprint, or None before the first full import."""
        data = await self._store.async_load()
        return data.get("tariff_fingerprint") if data else None

    async def async_get_first_hour(self) -> datetime | None:
        """Return the first hour the last full import costed, or None if it costed nothing."""
        data = await self._store.async_load()
        first_hour = data.get("first_hour") if data else None
        return datetime.fromisoformat(first_hour) if first_hour else None

    async def async_set_fingerprint(self, fingerprint: str, first_hour: datetime | None = None) -> None:
        """Record the fingerprint and first costed hour of a completed full import."""
        await self._store.async_save(
            {
                "tariff_fingerprint": fingerprint,
                "first_hour": first_hour.astimezone(timezone.utc).isoformat() if first_hour else None,
            }
        )

    async def async_remove(self) -> None:
        """Delete the store when the config entry is removed."""
        await self._store.async_remove()
