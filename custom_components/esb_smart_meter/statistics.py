"""Long-term statistics import for ESB Smart Meter integration."""

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import EnergyConverter

from .const import RECENT_IMPORT_DAYS
from .models import DUBLIN_TZ

_LOGGER = logging.getLogger(__name__)

# Imported (entity-based) statistics must declare "recorder" as their source.
RECORDER_SOURCE = "recorder"

# Up to 10 minutes for the recorder to commit an import (a first backfill on slow hardware)
COMMIT_WAIT_ATTEMPTS = 600
COMMIT_WAIT_DELAY = 1.0


async def async_import_hourly_statistics(
    hass: HomeAssistant,
    statistic_id: str,
    hourly: list[tuple[datetime, float]],
    *,
    unit: str = UnitOfEnergy.KILO_WATT_HOUR,
    name: str | None = None,
) -> float | None:
    """Import the hourly buckets and return the cumulative total (None if empty).

    Sums are anchored to the baseline recorded *before* the window, so the full
    window can be reimported each run without the recorder's "now" row skewing it.
    A `domain:object_id` statistic_id is added as an external statistic (no entity,
    so no Energy dashboard entity warnings); `name` labels it in statistic pickers.
    """
    if not hourly:
        return None

    # ESBData yields UTC-aware hours; naive input is treated as Irish local time
    localized = [(h if h.tzinfo else h.replace(tzinfo=DUBLIN_TZ), kwh) for h, kwh in hourly]
    running_sum = await _baseline(hass, statistic_id, localized[0][0])

    stats: list[StatisticData] = []
    for start, kwh in localized:
        running_sum += kwh
        stats.append(StatisticData(start=start, state=running_sum, sum=running_sum))

    external = ":" in statistic_id
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=statistic_id.split(":", 1)[0] if external else RECORDER_SOURCE,
        statistic_id=statistic_id,
        unit_of_measurement=unit,
        # Energy needs its unit class (HA 2026.11); currencies have no converter
        unit_class=EnergyConverter.UNIT_CLASS if unit == UnitOfEnergy.KILO_WATT_HOUR else None,
    )
    if external:
        async_add_external_statistics(hass, metadata, stats)
    else:
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


async def async_stored_hours(
    hass: HomeAssistant, statistic_id: str, start: datetime, end: datetime
) -> set[datetime]:
    """Return the starts (UTC) of the hourly rows stored for a statistic in [start, end)."""
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        {statistic_id},
        "hour",
        None,
        {"sum"},
    )
    return {datetime.fromtimestamp(row["start"], timezone.utc) for row in rows.get(statistic_id, [])}


async def async_wait_committed(
    hass: HomeAssistant,
    statistic_id: str,
    hour: datetime,
    expected_sum: float,
    *,
    attempts: int = COMMIT_WAIT_ATTEMPTS,
    delay: float = COMMIT_WAIT_DELAY,
) -> bool:
    """Wait until the imported row for `hour` is readable with `expected_sum`.

    The recorder writes imports on its own thread; the next import's baseline
    must read committed sums, so poll the last imported row rather than rely on
    recorder internals. Returns False (and warns) if it never shows up.
    """
    for attempt in range(attempts):
        rows = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            hour,
            hour + timedelta(hours=1),
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
        series = rows.get(statistic_id)
        if series and math.isclose(series[0].get("sum") or 0.0, expected_sum, abs_tol=1e-6):
            return True
        if attempt < attempts - 1:
            await asyncio.sleep(delay)
    _LOGGER.warning("Imported statistics for %s not readable after %d checks", statistic_id, attempts)
    return False


def recent_hours(
    hourly: list[tuple[datetime, float]],
    days: int = RECENT_IMPORT_DAYS,
    resume_from: datetime | None = None,
) -> list[tuple[datetime, float]]:
    """Return the buckets within `days` of the newest one (ESB data lags, so not 'now').

    `hourly` must be ascending. With `resume_from` (the first hour not yet stored),
    the window starts there instead when that is earlier, so an outage longer
    than `days` leaves no gap.
    """
    if not hourly:
        return []
    since = hourly[-1][0] - timedelta(days=days)
    if resume_from is not None:
        since = min(since, resume_from)
    return [(hour, value) for hour, value in hourly if hour >= since]
