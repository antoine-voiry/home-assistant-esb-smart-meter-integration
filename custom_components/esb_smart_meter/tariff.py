"""Tariff periods and hourly cost calculation."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_BOOST_END,
    CONF_BOOST_RATE,
    CONF_BOOST_START,
    CONF_DAY_RATE,
    CONF_EFFECTIVE_FROM,
    CONF_EXPORT_RATE,
    CONF_NIGHT_END,
    CONF_NIGHT_RATE,
    CONF_NIGHT_START,
    CONF_PEAK_END,
    CONF_PEAK_RATE,
    CONF_PEAK_START,
    CONF_PEAK_WEEKDAYS_ONLY,
    CONF_STANDING_CHARGE,
    SUBENTRY_TARIFF,
)
from .models import DUBLIN_TZ

Hourly = list[tuple[datetime, float]]

# Rate bands an hour can fall in, as named by TariffPeriod.band_at
BANDS = ("day", "night", "peak", "boost")


def _in_band(hour: int, start: int, end: int) -> bool:
    """Start inclusive, end exclusive; a band with start > end wraps midnight."""
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _band_hours(start: int, end: int) -> set[int]:
    return {hour for hour in range(24) if _in_band(hour, start, end)}


@dataclass(frozen=True)
class TariffPeriod:
    """One supplier tariff, in force from effective_from until the next period."""

    effective_from: date
    day_rate: float
    night_rate: float
    peak_rate: float
    standing_charge: float
    export_rate: float = 0.0
    night_start: int = 23
    night_end: int = 8
    peak_start: int = 17
    peak_end: int = 19
    peak_weekdays_only: bool = False
    # Optional cheap window (e.g. an EV night rate 02:00-05:00), every day, ahead of the other bands
    boost_rate: float | None = None
    boost_start: int = 2
    boost_end: int = 5

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> "TariffPeriod":
        """Build from subentry or form data (number selectors return floats)."""
        return cls(
            effective_from=date.fromisoformat(str(data[CONF_EFFECTIVE_FROM])),
            day_rate=float(data[CONF_DAY_RATE]),
            night_rate=float(data[CONF_NIGHT_RATE]),
            peak_rate=float(data[CONF_PEAK_RATE]),
            standing_charge=float(data[CONF_STANDING_CHARGE]),
            export_rate=float(data.get(CONF_EXPORT_RATE, 0.0)),
            night_start=int(data.get(CONF_NIGHT_START, 23)),
            night_end=int(data.get(CONF_NIGHT_END, 8)),
            peak_start=int(data.get(CONF_PEAK_START, 17)),
            peak_end=int(data.get(CONF_PEAK_END, 19)),
            peak_weekdays_only=bool(data.get(CONF_PEAK_WEEKDAYS_ONLY, False)),
            boost_rate=None if data.get(CONF_BOOST_RATE) in (None, "") else float(data[CONF_BOOST_RATE]),
            boost_start=int(data.get(CONF_BOOST_START, 2)),
            boost_end=int(data.get(CONF_BOOST_END, 5)),
        )

    def as_data(self) -> dict[str, Any]:
        """Return JSON-safe subentry data."""
        data = asdict(self)
        data[CONF_EFFECTIVE_FROM] = self.effective_from.isoformat()
        if self.boost_rate is None:
            # Leave periods without a boost window exactly as before (stored data and fingerprint)
            for key in (CONF_BOOST_RATE, CONF_BOOST_START, CONF_BOOST_END):
                del data[key]
        return data

    def band_at(self, local: datetime) -> str:
        """Return the band ("day", "night", "peak" or "boost") an Irish local time is billed in.

        Night or peak hours priced at the day rate are not a real band on the bill, so they count as day.
        """
        if self.boost_rate is not None and _in_band(local.hour, self.boost_start, self.boost_end):
            return "boost"
        if (
            self.peak_rate != self.day_rate
            and self._peak_applies_on(local)
            and _in_band(local.hour, self.peak_start, self.peak_end)
        ):
            return "peak"
        if self.night_rate != self.day_rate and _in_band(local.hour, self.night_start, self.night_end):
            return "night"
        return "day"

    def rate_at(self, local: datetime) -> float:
        """Return the €/kWh import rate for an Irish local time."""
        rate = getattr(self, f"{self.band_at(local)}_rate")
        return float(rate)

    def _peak_applies_on(self, local: datetime) -> bool:
        """Return False at weekends for a weekday-only peak."""
        return not self.peak_weekdays_only or local.weekday() < 5


def tariff_periods(entry: ConfigEntry) -> list[TariffPeriod]:
    """Return the entry's tariff periods, oldest first."""
    periods = [
        TariffPeriod.from_data(subentry.data)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TARIFF
    ]
    return sorted(periods, key=lambda period: period.effective_from)


def fingerprint(periods: list[TariffPeriod]) -> str:
    """Identify a set of periods, to notice when history must be re-costed."""
    payload = json.dumps([period.as_data() for period in periods], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _period_for(periods: list[TariffPeriod], day: date) -> TariffPeriod | None:
    current = None
    for period in periods:
        if period.effective_from > day:
            break
        current = period
    return current


def _costed_hours(hourly_usage: Hourly, periods: list[TariffPeriod]):
    """Yield (hour, local time, period, kWh) for every hour from the first costed hour to the newest."""
    if not hourly_usage or not periods:
        return
    usage = dict(hourly_usage)
    first_start = datetime.combine(periods[0].effective_from, time(), DUBLIN_TZ).astimezone(timezone.utc)
    hour = max(hourly_usage[0][0], first_start)
    while hour <= hourly_usage[-1][0]:
        local = hour.astimezone(DUBLIN_TZ)
        period = _period_for(periods, local.date())
        if period is not None:
            yield hour, local, period, usage.get(hour, 0.0)
        hour += timedelta(hours=1)


def usage_costs(hourly_usage: Hourly, periods: list[TariffPeriod]) -> Hourly:
    """Cost every hour from the first costed hour to the newest: kWh × rate + standing charge / 24."""
    return [
        (hour, kwh * period.rate_at(local) + period.standing_charge / 24)
        for hour, local, period, kwh in _costed_hours(hourly_usage, periods)
    ]


def band_usage(hourly_usage: Hourly, periods: list[TariffPeriod], band: str) -> Hourly:
    """The kWh of each costed hour billed in `band` (0 for hours in other bands)."""
    return [
        (hour, kwh if period.band_at(local) == band else 0.0)
        for hour, local, period, kwh in _costed_hours(hourly_usage, periods)
    ]


def band_costs(hourly_usage: Hourly, periods: list[TariffPeriod], band: str) -> Hourly:
    """The energy cost (no standing charge) of each costed hour billed in `band`."""
    return [
        (hour, kwh * period.rate_at(local) if period.band_at(local) == band else 0.0)
        for hour, local, period, kwh in _costed_hours(hourly_usage, periods)
    ]


def standing_charges(hourly_usage: Hourly, periods: list[TariffPeriod]) -> Hourly:
    """The standing charge of each costed hour; with the band costs it adds up to usage_costs."""
    return [(hour, period.standing_charge / 24) for hour, _, period, _ in _costed_hours(hourly_usage, periods)]


def export_credits(hourly_export: Hourly, periods: list[TariffPeriod]) -> Hourly:
    """Credit each export hour at the export rate in force."""
    credits: Hourly = []
    for hour, kwh in hourly_export:
        period = _period_for(periods, hour.astimezone(DUBLIN_TZ).date())
        if period is not None:
            credits.append((hour, kwh * period.export_rate))
    return credits


def validate_period(data: Mapping[str, Any], other_dates: set[date]) -> dict[str, str]:
    """Return form errors for a tariff period."""
    period = TariffPeriod.from_data(data)
    errors: dict[str, str] = {}
    if period.effective_from in other_dates:
        errors[CONF_EFFECTIVE_FROM] = "duplicate_period"
    if period.night_start == period.night_end:
        errors[CONF_NIGHT_END] = "empty_band"
    if period.peak_start == period.peak_end:
        errors[CONF_PEAK_END] = "empty_band"
    if not errors and _band_hours(period.peak_start, period.peak_end) & _band_hours(
        period.night_start, period.night_end
    ):
        errors["base"] = "peak_overlaps_night"
    if not errors and period.boost_rate is not None and period.boost_start == period.boost_end:
        errors[CONF_BOOST_END] = "empty_band"
    return errors
