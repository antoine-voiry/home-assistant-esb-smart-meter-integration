"""Tests for tariff periods and cost calculation."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.esb_smart_meter.models import DUBLIN_TZ
from custom_components.esb_smart_meter.tariff import (
    TariffPeriod,
    export_credits,
    fingerprint,
    tariff_periods,
    usage_costs,
    validate_period,
)

UTC = timezone.utc


def _period(**overrides):
    values = dict(
        effective_from=date(2026, 1, 1), day_rate=0.30, night_rate=0.20, peak_rate=0.40, standing_charge=0.72,
        export_rate=0.18,
    )
    values.update(overrides)
    return TariffPeriod(**values)


def _local(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=DUBLIN_TZ)


@pytest.mark.parametrize(
    "hour, expected",
    [(23, 0.20), (0, 0.20), (7, 0.20), (8, 0.30), (16, 0.30), (17, 0.40), (18, 0.40), (19, 0.30)],
)
def test_rate_bands_on_a_weekday(hour, expected):
    assert _period().rate_at(_local(2026, 10, 21, hour)) == expected  # Wednesday


def test_weekday_only_peak_is_day_rate_at_weekends():
    period = _period(peak_weekdays_only=True)
    assert period.rate_at(_local(2026, 10, 24, 17)) == 0.30  # Saturday
    assert period.rate_at(_local(2026, 10, 23, 17)) == 0.40  # Friday


def test_round_trip_through_subentry_data():
    period = _period(peak_weekdays_only=True)
    data = period.as_data()
    assert data["effective_from"] == "2026-01-01"
    assert TariffPeriod.from_data({**data, "night_start": 23.0}) == period  # selectors return floats


def test_tariff_periods_reads_sorted_tariff_subentries():
    later, earlier = _period(effective_from=date(2026, 10, 5)), _period()
    entry = SimpleNamespace(
        subentries={
            "a": SimpleNamespace(subentry_type="tariff", data=later.as_data()),
            "b": SimpleNamespace(subentry_type="tariff", data=earlier.as_data()),
            "c": SimpleNamespace(subentry_type="other", data={}),
        }
    )
    assert tariff_periods(entry) == [earlier, later]


def test_fingerprint_changes_with_rates_only():
    assert fingerprint([_period()]) == fingerprint([_period()])
    assert fingerprint([_period()]) != fingerprint([_period(day_rate=0.31)])


def test_usage_cost_uses_band_rate_plus_hourly_standing_charge():
    hour = datetime(2026, 10, 21, 17, tzinfo=UTC)  # 18:00 IST -> peak
    costs = usage_costs([(hour, 2.0)], [_period()])
    assert costs == [(hour, pytest.approx(2.0 * 0.40 + 0.72 / 24))]


def test_usage_cost_fills_hours_without_usage_with_standing_charge():
    start = datetime(2026, 10, 21, 0, tzinfo=UTC)
    costs = usage_costs([(start, 1.0), (start + timedelta(hours=3), 1.0)], [_period()])
    assert [h for h, _ in costs] == [start + timedelta(hours=i) for i in range(4)]
    assert costs[1][1] == pytest.approx(0.72 / 24)


def test_usage_cost_switches_period_on_local_midnight():
    old, new = _period(day_rate=0.30), _period(effective_from=date(2026, 10, 5), day_rate=0.50)
    before = datetime(2026, 10, 4, 10, tzinfo=UTC)  # 11:00 IST on the 4th
    after = datetime(2026, 10, 5, 10, tzinfo=UTC)
    costs = dict(usage_costs([(before, 1.0), (after, 1.0)], [old, new]))
    assert costs[before] == pytest.approx(0.30 + 0.03)
    assert costs[after] == pytest.approx(0.50 + 0.03)


def test_hours_before_first_period_are_not_costed():
    first = _period(effective_from=date(2026, 10, 5))
    hourly = [(datetime(2026, 10, 4, 12, tzinfo=UTC), 1.0), (datetime(2026, 10, 5, 12, tzinfo=UTC), 1.0)]
    hours = [h for h, _ in usage_costs(hourly, [first])]
    # Costing starts at local midnight of the period's start date (23:00 UTC during Irish summer time)
    assert hours[0] == datetime(2026, 10, 4, 23, tzinfo=UTC)
    assert hours[-1] == datetime(2026, 10, 5, 12, tzinfo=UTC)
    assert datetime(2026, 10, 4, 12, tzinfo=UTC) not in hours
    assert usage_costs(hourly, []) == []
    assert usage_costs([], [first]) == []


def test_dst_fall_back_day_costs_both_one_am_hours_at_night_rate():
    # 25 Oct 2026: 01:00-02:00 local happens twice (00:00 and 01:00 UTC)
    hourly = [(datetime(2026, 10, 25, 0, tzinfo=UTC), 1.0), (datetime(2026, 10, 25, 1, tzinfo=UTC), 1.0)]
    costs = usage_costs(hourly, [_period()])
    assert [c for _, c in costs] == [pytest.approx(0.20 + 0.03)] * 2


def test_export_credit_uses_export_rate_without_standing_charge():
    hour = datetime(2026, 10, 21, 12, tzinfo=UTC)
    assert export_credits([(hour, 2.0)], [_period()]) == [(hour, pytest.approx(0.36))]
    assert export_credits([(hour, 2.0)], []) == []
    assert export_credits([(datetime(2025, 12, 1, tzinfo=UTC), 1.0)], [_period()]) == []


def _form(**overrides):
    data = _period().as_data()
    data.update(overrides)
    return data


def test_validate_period_accepts_standard_bands():
    assert validate_period(_form(), set()) == {}


def test_validate_period_rejects_duplicate_date():
    assert validate_period(_form(), {date(2026, 1, 1)}) == {"effective_from": "duplicate_period"}


def test_validate_period_rejects_empty_bands_and_overlap():
    assert validate_period(_form(night_end=23), set())["night_end"] == "empty_band"
    assert validate_period(_form(peak_end=17), set())["peak_end"] == "empty_band"
    assert validate_period(_form(peak_start=6, peak_end=9), set()) == {"base": "peak_overlaps_night"}
