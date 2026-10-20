"""Tariff periods and hourly cost calculation."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
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
        )

    def as_data(self) -> dict[str, Any]:
        """Return JSON-safe subentry data."""
        data = asdict(self)
        data[CONF_EFFECTIVE_FROM] = self.effective_from.isoformat()
        return data

    def rate_at(self, local: datetime) -> float:
        """Return the €/kWh import rate for an Irish local time."""
        peak_today = not (self.peak_weekdays_only and local.weekday() >= 5)
        if peak_today and _in_band(local.hour, self.peak_start, self.peak_end):
            return self.peak_rate
        if _in_band(local.hour, self.night_start, self.night_end):
            return self.night_rate
        return self.day_rate


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


def usage_costs(hourly_usage: Hourly, periods: list[TariffPeriod]) -> Hourly:
    """Cost every hour from the first costed hour to the newest: kWh × rate + standing charge / 24."""
    if not hourly_usage or not periods:
        return []
    usage = dict(hourly_usage)
    first_start = datetime.combine(periods[0].effective_from, time(), DUBLIN_TZ).astimezone(timezone.utc)
    hour = max(hourly_usage[0][0], first_start)
    costs: Hourly = []
    while hour <= hourly_usage[-1][0]:
        local = hour.astimezone(DUBLIN_TZ)
        period = _period_for(periods, local.date())
        if period is not None:
            costs.append((hour, usage.get(hour, 0.0) * period.rate_at(local) + period.standing_charge / 24))
        hour += timedelta(hours=1)
    return costs


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
    return errors
