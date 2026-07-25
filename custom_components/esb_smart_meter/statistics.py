"""Long-term statistics import for ESB Smart Meter integration."""

import logging
from datetime import datetime, timedelta

import homeassistant.util.dt as dt_util
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

    localized = [(h.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE), kwh) for h, kwh in hourly]
    running_sum = await _baseline(hass, statistic_id, localized[0][0])

    stats: list[StatisticData] = []
    for start, kwh in localized:
        running_sum += kwh
        stats.append(StatisticData(start=start, state=running_sum, sum=running_sum))

    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=None,
        source=RECORDER_SOURCE,
        statistic_id=statistic_id,
        unit_of_measurement="kWh",
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
