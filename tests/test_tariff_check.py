"""Repair issue while an entry has no tariff period, so its cost statistics stay empty."""

from homeassistant.config_entries import ConfigSubentry, ConfigSubentryData
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esb_smart_meter.const import CONF_MPRN, DOMAIN, SUBENTRY_TARIFF
from custom_components.esb_smart_meter.tariff_check import async_check_tariff, async_remove_tariff_issue

MPRN = "10301831754"
TARIFF = {
    "effective_from": "2026-01-01",
    "day_rate": 0.30,
    "night_rate": 0.15,
    "peak_rate": 0.40,
    "standing_charge": 0.70,
}


def _entry(hass, subentries=()):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MPRN, data={CONF_MPRN: MPRN}, subentries_data=list(subentries))
    entry.add_to_hass(hass)
    return entry


def _issue(hass, entry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"no_tariff_{entry.entry_id}")


async def test_entry_without_tariff_raises_issue(hass):
    entry = _entry(hass)
    async_check_tariff(hass, entry)
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "no_tariff"
    assert issue.translation_placeholders == {"mprn": MPRN}
    assert issue.severity == ir.IssueSeverity.WARNING
    assert not issue.is_fixable


async def test_entry_with_tariff_has_no_issue(hass):
    entry = _entry(hass, [ConfigSubentryData(data=TARIFF, subentry_type=SUBENTRY_TARIFF, title="t", unique_id="2026-01-01")])
    async_check_tariff(hass, entry)
    assert _issue(hass, entry) is None


async def test_adding_a_tariff_clears_issue(hass):
    entry = _entry(hass)
    async_check_tariff(hass, entry)
    hass.config_entries.async_add_subentry(
        entry, ConfigSubentry(data=TARIFF, subentry_type=SUBENTRY_TARIFF, title="t", unique_id="2026-01-01")
    )
    async_check_tariff(hass, entry)
    assert _issue(hass, entry) is None


async def test_remove_tariff_issue(hass):
    entry = _entry(hass)
    async_check_tariff(hass, entry)
    async_remove_tariff_issue(hass, entry.entry_id)
    assert _issue(hass, entry) is None
