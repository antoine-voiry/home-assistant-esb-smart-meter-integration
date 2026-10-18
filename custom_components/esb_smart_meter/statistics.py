"""Long-term statistics import for ESB Smart Meter integration."""

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import EnergyConverter

from .models import DUBLIN_TZ

_LOGGER = logging.getLogger(__name__)

# Imported (entity-based) statistics must declare "recorder" as their source.
RECORDER_SOURCE = "recorder"


async def async_import_hourly_statistics(
    hass: HomeAssistant,
    statistic_id: str,
    hourly: list[tuple[datetime, float]],
) -> float | None:
    """Import the hourly buckets and return the cumulative total (None if empty).

    Sums are anchored to the baseline recorded *before* the window, so the full
    window can be reimported each run without the recorder's "now" row skewing it.
    """
    if not hourly:
        return None

    # ESBData yields UTC-aware hours; naive input is treated as Irish local time
    localized = [(h if h.tzinfo else h.replace(tzinfo=DUBLIN_TZ), kwh) for h, kwh in hourly]
    running_sum = await _baseline(hass, statistic_id, localized[0][0])
    retained_gap_energy = await _retained_gap_energy(hass, statistic_id, localized, running_sum)

    stats: list[StatisticData] = []
    for start, kwh in localized:
        # Hours the capture omitted but the recorder already stored keep their
        # energy in the running total, so later cumulative rows never fall
        # below the retained row they follow.
        running_sum += retained_gap_energy.get(start, 0.0) + kwh
        stats.append(StatisticData(start=start, state=running_sum, sum=running_sum))

    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=None,
        source=RECORDER_SOURCE,
        statistic_id=statistic_id,
        unit_of_measurement="kWh",
        unit_class=EnergyConverter.UNIT_CLASS,
    )
    async_import_statistics(hass, metadata, stats)
    _LOGGER.debug("Imported %d statistics rows to %s (total %.3f)", len(stats), statistic_id, running_sum)
    return running_sum


async def _baseline(hass: HomeAssistant, statistic_id: str, window_start: datetime) -> float:
    """Return the cumulative sum of the last statistic recorded before the window."""
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        window_start - timedelta(days=366),
        window_start,
        {statistic_id},
        "hour",
        None,
        {"sum"},
    )
    series = rows.get(statistic_id)
    if series:
        return float(series[-1].get("sum") or 0.0)
    return 0.0


async def _retained_gap_energy(
    hass: HomeAssistant,
    statistic_id: str,
    localized: list[tuple[datetime, float]],
    baseline: float,
) -> dict[datetime, float]:
    """Energy already stored for internal hours the current capture omitted.

    The complete-hour filter drops an hour whose half-hour reading is missing,
    but the recorder keeps that hour's previously imported row. Its energy has
    to be carried into later cumulative rows or the rewritten suffix drops below
    the retained row. Only stored evidence is used: an hour that was never
    recorded stays unknown and contributes nothing, never a fabricated zero.
    """
    starts = [start for start, _ in localized]
    if not any(later - earlier > timedelta(hours=1) for earlier, later in zip(starts, starts[1:])):
        return {}

    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        starts[0],
        starts[-1],
        {statistic_id},
        "hour",
        None,
        {"sum"},
    )
    series = rows.get(statistic_id) or []
    admitted = set(starts)
    energy: dict[datetime, float] = {}
    previous_sum = baseline
    for row in series:
        start = datetime.fromtimestamp(row["start"], timezone.utc)
        stored_sum = float(row.get("sum") or 0.0)
        if start in admitted:
            previous_sum = stored_sum
            continue
        # Attribute the retained hour's stored energy to the next imported hour.
        for next_start in starts:
            if next_start > start:
                energy[next_start] = energy.get(next_start, 0.0) + stored_sum - previous_sum
                break
        previous_sum = stored_sum
    return energy
