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
    MAX_DATA_AGE_DAYS,
    READ_TYPE_EXPORT,
    READ_TYPE_IMPORT,
)

_LOGGER = logging.getLogger(__name__)

# ESB labels are interval-END wall-clock times in Irish local time
DUBLIN_TZ = ZoneInfo("Europe/Dublin")


class ESBData:
    """Class to manipulate data retrieved from ESB with memory optimization."""

    def __init__(self, *, data: List[Dict[str, Any]]) -> None:
        """Initialize with raw CSV data, filtering old data to prevent memory leaks."""
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

        # Filter out data older than MAX_DATA_AGE_DAYS to prevent memory leaks
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=MAX_DATA_AGE_DAYS)
        self._data, self._export_data = self._filter_and_parse_data(data, cutoff_date)
        _LOGGER.debug(
            "Loaded %d import / %d export rows (filtered data older than %d days)",
            len(self._data),
            len(self._export_data),
            MAX_DATA_AGE_DAYS,
        )

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
        self, data: list[dict[str, Any]], cutoff_date: datetime
    ) -> tuple[list[tuple[datetime, float]], list[tuple[datetime, float]]]:
        """Filter old data and split rows into import and export series."""
        import_data: list[tuple[datetime, float]] = []
        export_data: list[tuple[datetime, float]] = []
        unknown_types: dict[str, int] = defaultdict(int)
        invalid_values = 0
        nonexistent_labels = 0
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

        # First pass: recognize the read type and classify each Dublin wall-clock
        # label before validating the numeric value, so an invalid value cannot
        # shrink an autumn label group that must be judged as a whole.
        prepared: list[tuple[datetime, float, str, str]] = []
        repeated: dict[tuple[str, datetime], int] = defaultdict(int)
        admitted_repeated: dict[tuple[str, datetime], int] = defaultdict(int)
        for row, label in ordered:
            if label is None:
                _LOGGER.warning("Skipping invalid row: unparseable date %r", row.get(CSV_COLUMN_DATE))
                continue
            read_type = str(row.get(CSV_COLUMN_READ_TYPE, "")).strip()
            if READ_TYPE_IMPORT in read_type:
                series = READ_TYPE_IMPORT
            elif READ_TYPE_EXPORT in read_type:
                series = READ_TYPE_EXPORT
            else:
                unknown_types[read_type] += 1
                continue
            kind = self._classify_label(label)
            if kind == "nonexistent":
                nonexistent_labels += 1
                continue
            if kind == "repeated":
                # Count every recognized occurrence, even one whose value is
                # later rejected, so a three-or-more-row group is refused whole.
                repeated[(series, label)] += 1
            try:
                value = float(row[CSV_COLUMN_VALUE])
                if not math.isfinite(value) or value < 0:
                    invalid_values += 1
                    continue
            except (ValueError, KeyError) as err:
                _LOGGER.warning("Skipping invalid row: %s", err)
                continue
            if kind == "repeated":
                admitted_repeated[(series, label)] += 1
            prepared.append((label, value, series, kind))

        # Second pass: resolve admitted labels to UTC, refusing repeated labels
        # that are not part of a supported pair, then apply the rolling cutoff.
        seen_repeated: dict[tuple[str, datetime], int] = defaultdict(int)
        refused_repeated = 0
        for label, value, series, kind in prepared:
            if kind == "repeated":
                key = (series, label)
                # The raw group must be exactly two rows and both folds must
                # survive numeric validation; otherwise refuse the whole group.
                if repeated[key] != 2 or admitted_repeated[key] != 2:
                    refused_repeated += 1
                    continue
                fold = seen_repeated[key]
                seen_repeated[key] += 1
                timestamp = label.replace(tzinfo=DUBLIN_TZ, fold=fold).astimezone(timezone.utc)
            else:
                timestamp = label.replace(tzinfo=DUBLIN_TZ).astimezone(timezone.utc)
            if timestamp < cutoff_date:
                continue
            if series == READ_TYPE_IMPORT:
                import_data.append((timestamp, value))
            else:
                export_data.append((timestamp, value))
        import_data.sort(key=lambda item: item[0])
        export_data.sort(key=lambda item: item[0])
        if nonexistent_labels:
            _LOGGER.warning(
                "Skipped %d rows labelled with a nonexistent Dublin civil time", nonexistent_labels
            )
        if refused_repeated:
            _LOGGER.warning(
                "Skipped %d rows in unsupported repeated autumn label groups", refused_repeated
            )
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
    def _classify_label(naive: datetime) -> str:
        """Classify a Dublin wall-clock label using its two fold candidates.

        A label that does not survive a UTC round trip never occurs locally (the
        spring-forward gap). A label whose two folds carry different offsets
        occurs twice (the autumn fall-back). Labels are interval-END times, so a
        repeated label is only resolvable from its position in an ordered pair.
        """
        first = naive.replace(tzinfo=DUBLIN_TZ, fold=0)
        second = naive.replace(tzinfo=DUBLIN_TZ, fold=1)
        if first.astimezone(timezone.utc).astimezone(DUBLIN_TZ).replace(tzinfo=None) != naive:
            return "nonexistent"
        if first.utcoffset() != second.utcoffset():
            return "repeated"
        return "unique"

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
        """Aggregate 30-minute readings into complete hourly buckets, ascending.

        A bucket is admitted only once both of its interval-end readings are
        present. The rolling 90-day cutoff lands inside an hour, so the leading
        bucket can hold a single half hour; importing it would replace a
        previously complete stored hour with a lower sum and shift the whole
        later cumulative suffix. Incomplete buckets (leading, trailing or a gap)
        are omitted rather than imported as partial or zero energy.
        """
        buckets: dict[datetime, float] = defaultdict(float)
        ends: dict[datetime, set[datetime]] = defaultdict(set)
        for timestamp, value in series:
            # ESB timestamps are interval end times, so bucket by interval start
            hour_start = (timestamp - timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
            buckets[hour_start] += value
            ends[hour_start].add(timestamp)
        return sorted(
            (start, kwh)
            for start, kwh in buckets.items()
            if ends[start] == {start + timedelta(minutes=30), start + timedelta(hours=1)}
        )

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
