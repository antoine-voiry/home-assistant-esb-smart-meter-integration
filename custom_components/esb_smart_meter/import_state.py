"""Remembers which tariffs each cost series was last fully imported with."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
COST_SERIES = ("usage_cost", "export_credit")


class ImportState:
    """Per-entry, per-series store holding the last fully imported tariff fingerprint."""

    def __init__(self, hass: HomeAssistant, entry_id: str, series: str) -> None:
        """Initialize the store for one series of one entry."""
        self._store: Store[dict[str, str]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.{series}")

    async def async_get_fingerprint(self) -> str | None:
        """Return the stored fingerprint, or None before the first full import."""
        data = await self._store.async_load()
        return data.get("tariff_fingerprint") if data else None

    async def async_set_fingerprint(self, fingerprint: str) -> None:
        """Record the fingerprint used by a completed full import."""
        await self._store.async_save({"tariff_fingerprint": fingerprint})

    async def async_remove(self) -> None:
        """Delete the store when the config entry is removed."""
        await self._store.async_remove()
