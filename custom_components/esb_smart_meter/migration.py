"""Move the entity-based History statistics to external statistics."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .statistics import async_copy_statistics

_LOGGER = logging.getLogger(__name__)


async def async_migrate_legacy_history(
    hass: HomeAssistant, legacy_unique_id: str, statistic_id: str, *, unit: str, name: str
) -> None:
    """Copy an old History entity's statistics to `statistic_id`, repoint the Energy dashboard, remove the entity.

    Runs once: afterwards the entity is gone, so there is nothing to find. If the copy
    can't be confirmed, nothing else changes and the next start tries again. The old
    statistics are left in place for the user to delete from Developer tools.
    """
    registry = er.async_get(hass)
    old_entity_id = registry.async_get_entity_id("sensor", DOMAIN, legacy_unique_id)
    if old_entity_id is None:
        return
    copied = await async_copy_statistics(hass, old_entity_id, statistic_id, unit=unit, name=name)
    if copied is None:
        _LOGGER.warning("Could not move %s statistics to %s; will retry on next start", old_entity_id, statistic_id)
        return
    await _async_repoint_energy_dashboard(hass, old_entity_id, statistic_id)
    registry.async_remove(old_entity_id)
    _LOGGER.info("Moved %d hours of %s to %s", copied, old_entity_id, statistic_id)


async def _async_repoint_energy_dashboard(hass: HomeAssistant, old_id: str, new_id: str) -> None:
    """Replace `old_id` with `new_id` wherever the Energy dashboard references it."""
    if "energy" not in hass.config.components:
        return
    from homeassistant.components.energy.data import async_get_manager  # noqa: PLC0415

    manager = await async_get_manager(hass)
    if not manager.data:
        return
    sources = _replace(manager.data["energy_sources"], old_id, new_id)
    if sources != manager.data["energy_sources"]:
        await manager.async_update({"energy_sources": sources})
        _LOGGER.info("Energy dashboard now uses %s instead of %s", new_id, old_id)


def _replace(value: Any, old: str, new: str) -> Any:
    """Return a copy of nested dicts/lists with every `old` string replaced by `new`."""
    if isinstance(value, dict):
        return {key: _replace(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, old, new) for item in value]
    return new if value == old else value
