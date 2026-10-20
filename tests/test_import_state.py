"""Tests for the per-series import state store."""

from datetime import datetime, timezone

import pytest

from custom_components.esb_smart_meter.import_state import ImportState


@pytest.mark.asyncio
async def test_fingerprint_round_trip_and_remove(hass):
    state = ImportState(hass, "entry1", "usage_cost")
    assert await state.async_get_fingerprint() is None

    await state.async_set_fingerprint("abc")
    assert await ImportState(hass, "entry1", "usage_cost").async_get_fingerprint() == "abc"
    assert await ImportState(hass, "entry1", "export_credit").async_get_fingerprint() is None

    await state.async_remove()
    assert await ImportState(hass, "entry1", "usage_cost").async_get_fingerprint() is None


@pytest.mark.asyncio
async def test_first_hour_stored_with_fingerprint(hass):
    state = ImportState(hass, "entry1", "usage_cost")
    assert await state.async_get_first_hour() is None

    first = datetime(2026, 1, 11, tzinfo=timezone.utc)
    await state.async_set_fingerprint("abc", first)
    reloaded = ImportState(hass, "entry1", "usage_cost")
    assert await reloaded.async_get_fingerprint() == "abc"
    assert await reloaded.async_get_first_hour() == first

    await state.async_set_fingerprint("def", None)
    assert await ImportState(hass, "entry1", "usage_cost").async_get_first_hour() is None
