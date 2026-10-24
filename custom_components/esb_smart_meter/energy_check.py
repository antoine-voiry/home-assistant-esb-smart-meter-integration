"""Raise a Repair issue when the Energy dashboard uses ESB entities instead of ESB statistics.

The meter's history lives in external statistics (``esb_smart_meter:usage_<mprn>`` and
friends). No ESB entity carries usable history: period sensors cover moving windows,
the totals are display-only, and entities left over from older versions stopped
updating. The Energy dashboard then shows nothing, so tell the user what to pick.
"""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import CONF_MPRN, DOMAIN

ISSUE_KEY = "energy_dashboard_entities"
# Sensors the energy integration derives from a source entity when a price is set
_DERIVED_SUFFIXES = ("_cost", "_compensation")
_LISTENING = f"{DOMAIN}_energy_check_listening"


async def async_setup_energy_check(hass: HomeAssistant) -> None:
    """Check now, and again whenever the Energy settings change.

    The energy manager has no way to stop listening, so one listener per Home
    Assistant run checks every ESB entry.
    """
    if "energy" not in hass.config.components:
        return
    from homeassistant.components.energy.data import async_get_manager  # noqa: PLC0415

    if not hass.data.get(_LISTENING):
        hass.data[_LISTENING] = True
        manager = await async_get_manager(hass)
        manager.async_listen_updates(lambda: async_check_energy_dashboard(hass))
    await async_check_energy_dashboard(hass)


async def async_check_energy_dashboard(hass: HomeAssistant) -> None:
    """Create or clear each ESB entry's issue from the current Energy settings."""
    if "energy" not in hass.config.components:
        return
    from homeassistant.components.energy.data import async_get_manager  # noqa: PLC0415

    manager = await async_get_manager(hass)
    referenced = _referenced_statistics(manager.data or {})
    registry = er.async_get(hass)
    for entry in hass.config_entries.async_entries(DOMAIN):
        issue_id = f"{ISSUE_KEY}_{entry.entry_id}"
        misused = sorted(s for s in referenced if _is_entry_entity(registry, s, entry.entry_id))
        if not misused:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            continue
        meter = f"ESB Smart Meter ({entry.data[CONF_MPRN]})"
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_KEY,
            translation_placeholders={
                "entities": ", ".join(misused),
                "usage": f"{meter} Usage",
                "export": f"{meter} Export",
                "usage_cost": f"{meter} Usage cost",
                "export_credit": f"{meter} Export credit",
            },
        )


def async_remove_energy_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Drop the entry's issue when the entry is removed."""
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_KEY}_{entry_id}")


def _referenced_statistics(value: Any) -> set[str]:
    """Return every statistic id in the Energy settings (``stat_*`` keys, at any depth)."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.startswith("stat_") and isinstance(item, str):
                found.add(item)
            else:
                found |= _referenced_statistics(item)
    elif isinstance(value, list):
        for item in value:
            found |= _referenced_statistics(item)
    return found


def _is_entry_entity(registry: er.EntityRegistry, statistic_id: str, entry_id: str) -> bool:
    """Return True for one of the entry's entities, or a cost sensor the energy integration derived from one."""
    candidates = [statistic_id] + [
        statistic_id.removesuffix(suffix) for suffix in _DERIVED_SUFFIXES if statistic_id.endswith(suffix)
    ]
    for entity_id in candidates:
        entity = registry.async_get(entity_id)
        if entity is not None and entity.platform == DOMAIN and entity.config_entry_id == entry_id:
            return True
    return False
