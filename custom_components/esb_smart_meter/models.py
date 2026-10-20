"""Data models for ESB Smart Meter integration."""

import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from .const import (
    CSV_COLUMN_DATE,
    CSV_COLUMN_READ_TYPE,
    CSV_COLUMN_VALUE,
    CSV_DATE_FORMAT,
    READ_TYPE_EXPORT,
    READ_TYPE_IMPORT,
)

_LOGGER = logging.getLogger(__name__)

# ESB labels are interval-END wall-clock times in Irish local time
DUBLIN_TZ = ZoneInfo("Europe/Dublin")


class ESBData:
    """Class to manipulate data retrieved from ESB with memory optimization."""

    def __init__(self, *, data: List[Dict[str, Any]]) -> None:
        """Initialize with raw CSV data."""
        # Validate CSV structure
        if data:
            if not self._validate_csv_structure(data[0]):
                _LOGGER.error("CSV validation failed. First row keys: %s", list(data[0].keys()))
                _LOGGER.error(
                    "Expected columns: %s, %s, %s", CSV_COLUMN_DATE, CSV_COLUMN_VALUE, CSV_COLUMN_READ_TYPE
                )
                raise ValueError(
                    f"Invalid CSV structure. Expected columns: "
                    f"{CSV_COLUMN_DATE}, {CSV_COLUMN_VALUE}, {CSV_COLUMN_READ_TYPE}"
                )

        # ESB returns a rolling ~2 years; keep it all for the statistics backfill
        self._data, self._export_data = self._filter_and_parse_data(data)
        _LOGGER.debug("Loaded %d import / %d export rows", len(self._data), len(self._export_data))

    @staticmethod
    def _validate_csv_structure(row: dict[str, Any]) -> bool:
        """Validate that required CSV columns exist."""
        required_columns = [CSV_COLUMN_DATE, CSV_COLUMN_VALUE, CSV_COLUMN_READ_TYPE]
        available_columns = list(row.keys())
        has_required = all(col in row for col in required_columns)

        if not has_required:
            _LOGGER.error("CSV validation failed. Required: %s, Available: %s", required_columns, available_columns)

        return has_required

    def _filter_and_parse_data(
        self, data: list[dict[str, Any]]
    ) -> tuple[list[tuple[datetime, float]], list[tuple[datetime, float]]]:
        """Parse rows and split them into import and export series."""
        import_data: list[tuple[datetime, float]] = []
        export_data: list[tuple[datetime, float]] = []
        unknown_types: dict[str, int] = defaultdict(int)
        invalid_values = 0
        # ESB returns newest rows first; fold detection needs chronological order
        parsed_labels: list[datetime | None] = []
        for row in data:
            try:
                parsed_labels.append(datetime.strptime(row[CSV_COLUMN_DATE], CSV_DATE_FORMAT))
            except (ValueError, KeyError):
                parsed_labels.append(None)
        valid = [label for label in parsed_labels if label is not None]
        ordered = list(zip(data, parsed_labels))
        if len(valid) > 1 and valid[0] > valid[-1]:
            ordered.reverse()
        seen_ambiguous: set[tuple[str, datetime]] = set()
        for row, label in ordered:
            try:
                if label is None:
                    raise ValueError(f"unparseable date {row.get(CSV_COLUMN_DATE)!r}")
                value = float(row[CSV_COLUMN_VALUE])
                if not math.isfinite(value) or value < 0:
                    invalid_values += 1
                    continue
                read_type = str(row.get(CSV_COLUMN_READ_TYPE, "")).strip()
                timestamp = self._localize(label, seen_ambiguous, read_type)
                if READ_TYPE_IMPORT in read_type:
                    import_data.append((timestamp, value))
                elif READ_TYPE_EXPORT in read_type:
                    export_data.append((timestamp, value))
                else:
                    unknown_types[read_type] += 1
            except (ValueError, KeyError) as err:
                _LOGGER.warning("Skipping invalid row: %s", err)
                continue
        import_data.sort(key=lambda item: item[0])
        export_data.sort(key=lambda item: item[0])
        if unknown_types:
            _LOGGER.warning(
                "Skipped %d rows with unrecognized Read Type: %s",
                sum(unknown_types.values()),
                sorted(unknown_types),
            )
        if invalid_values:
            _LOGGER.warning("Skipped %d rows with a non-finite or negative Read Value", invalid_values)
        return import_data, export_data

    @staticmethod
    def _localize(naive: datetime, seen_ambiguous: set[tuple[str, datetime]], read_type: str) -> datetime:
        """Resolve a Dublin wall-clock label to UTC.

        On the October fall-back day the 01:00-02:00 labels occur twice; the first
        occurrence in chronological order is summer time (fold=0), the second is
        winter time (fold=1).
        """
        first = naive.replace(tzinfo=DUBLIN_TZ, fold=0)
        second = naive.replace(tzinfo=DUBLIN_TZ, fold=1)
        if first.utcoffset() == second.utcoffset():
            return first.astimezone(timezone.utc)
        key = (read_type, naive)
        if key in seen_ambiguous:
            return second.astimezone(timezone.utc)
        seen_ambiguous.add(key)
        return first.astimezone(timezone.utc)

    @staticmethod
    def _as_utc(moment: datetime) -> datetime:
        """Treat naive datetimes as Dublin local time and convert to UTC."""
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=DUBLIN_TZ)
        return moment.astimezone(timezone.utc)

    @classmethod
    def _sum_since(cls, series: list[tuple[datetime, float]], since: datetime) -> float:
        """Sum values in a series since a specific datetime."""
        since_utc = cls._as_utc(since)
        return sum(value for timestamp, value in series if timestamp >= since_utc)

    @classmethod
    def _readings_since(cls, series: list[tuple[datetime, float]], since: datetime) -> list[dict[str, Any]]:
        """Return readings in a series since a specific datetime (Dublin local ISO timestamps)."""
        since_utc = cls._as_utc(since)
        return [
            {"timestamp": timestamp.astimezone(DUBLIN_TZ).isoformat(), "value": value}
            for timestamp, value in series
            if timestamp >= since_utc
        ]

    @staticmethod
    def start_of_today() -> datetime:
        return datetime.now(DUBLIN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def since_24_hours() -> datetime:
        return datetime.now(DUBLIN_TZ) - timedelta(days=1)

    @staticmethod
    def start_of_week() -> datetime:
        now = datetime.now(DUBLIN_TZ)
        return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())

    @staticmethod
    def since_7_days() -> datetime:
        return datetime.now(DUBLIN_TZ) - timedelta(days=7)

    @staticmethod
    def start_of_month() -> datetime:
        return datetime.now(DUBLIN_TZ).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def since_30_days() -> datetime:
        return datetime.now(DUBLIN_TZ) - timedelta(days=30)

    def get_readings_since(self, *, since: datetime) -> list[dict[str, Any]]:
        """Get import readings since a specific datetime."""
        return self._readings_since(self._data, since)

    def get_export_readings_since(self, *, since: datetime) -> list[dict[str, Any]]:
        """Get export readings since a specific datetime."""
        return self._readings_since(self._export_data, since)

    def hourly_usage(self) -> list[tuple[datetime, float]]:
        """Get usage readings aggregated into hourly (hour_start, kWh) buckets."""
        return self._hourly(self._data)

    def hourly_export(self) -> list[tuple[datetime, float]]:
        """Get export readings aggregated into hourly (hour_start, kWh) buckets."""
        return self._hourly(self._export_data)

    @staticmethod
    def _hourly(series: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
        """Aggregate 30-minute readings into hourly buckets, ascending."""
        buckets: dict[datetime, float] = defaultdict(float)
        for timestamp, value in series:
            # ESB timestamps are interval end times, so bucket by interval start
            hour_start = (timestamp - timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
            buckets[hour_start] += value
        return sorted(buckets.items())

    @property
    def today(self) -> float:
        """Get today's usage."""
        return self._sum_since(self._data, self.start_of_today())

    @property
    def last_24_hours(self) -> float:
        """Get last 24 hours usage."""
        return self._sum_since(self._data, self.since_24_hours())

    @property
    def this_week(self) -> float:
        """Get this week's usage."""
        return self._sum_since(self._data, self.start_of_week())

    @property
    def last_7_days(self) -> float:
        """Get last 7 days usage."""
        return self._sum_since(self._data, self.since_7_days())

    @property
    def this_month(self) -> float:
        """Get this month's usage."""
        return self._sum_since(self._data, self.start_of_month())

    @property
    def last_30_days(self) -> float:
        """Get last 30 days usage."""
        return self._sum_since(self._data, self.since_30_days())

    @property
    def export_today(self) -> float:
        """Get today's export."""
        return self._sum_since(self._export_data, self.start_of_today())

    @property
    def export_last_24_hours(self) -> float:
        """Get last 24 hours export."""
        return self._sum_since(self._export_data, self.since_24_hours())

    @property
    def export_this_week(self) -> float:
        """Get this week's export."""
        return self._sum_since(self._export_data, self.start_of_week())

    @property
    def export_last_7_days(self) -> float:
        """Get last 7 days export."""
        return self._sum_since(self._export_data, self.since_7_days())

    @property
    def export_this_month(self) -> float:
        """Get this month's export."""
        return self._sum_since(self._export_data, self.start_of_month())

    @property
    def export_last_30_days(self) -> float:
        """Get last 30 days export."""
        return self._sum_since(self._export_data, self.since_30_days())
