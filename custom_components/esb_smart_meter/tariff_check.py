"""Raise a Repair issue while an entry has no tariff period.

Without one, the cost statistics (usage cost, export credit, the band costs and the
standing charge) stay empty with nothing saying why.
"""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import CONF_MPRN, DOMAIN
from .tariff import tariff_periods

ISSUE_KEY = "no_tariff"


@callback
def async_check_tariff(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create or clear the entry's issue from its tariff periods."""
    if tariff_periods(entry):
        async_remove_tariff_issue(hass, entry.entry_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry.entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_KEY,
        translation_placeholders={"mprn": entry.data[CONF_MPRN]},
    )


@callback
def async_remove_tariff_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Drop the entry's issue (tariff added, or entry removed).

    Not on unload: deleting an issue forgets that the user chose to ignore it.
    """
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id))


def _issue_id(entry_id: str) -> str:
    return f"{ISSUE_KEY}_{entry_id}"
